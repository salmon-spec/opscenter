"""RabbitMQ 异步任务队列（v5.0.0）。

职责：
- `publish(task_type, payload)`：把任务投递到主队列（持久化消息），返回 queued 信息；
- `register_handler(task_type, fn)` + `start_worker()`：后台线程消费，ack/nack，
  失败进死信队列（`MQ_DLQ`）；handler 抛异常 → nack(requeue=False)；
- `MW_LEADER_ENABLED=true` 时 worker 先经 Redis 租约（`leader:mq-worker`）选主，
  避免多副本重复消费；
- `queues()` 经 Management HTTP API 返回队列明细，供数据服务页面使用；
  未配置 MQ_MANAGEMENT_URL 时从 MQ_URL 推导 host:15672，凭证取 URL 内 user:pass。

环境变量：MQ_URL、MQ_QUEUE、MQ_DLQ、MQ_MANAGEMENT_URL、MQ_ENABLED、MW_LEADER_ENABLED。
实现：pika 1.3.2 BlockingConnection，心跳 60s，断线指数退避重连（1s→30s 上限），
stop_worker() 必须能在 3 秒内退出。所有公开方法 fail-open，不抛异常。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

import httpx
import pika

from app.config import (
    MQ_DLQ,
    MQ_ENABLED,
    MQ_MANAGEMENT_URL,
    MQ_QUEUE,
    MQ_URL,
    MW_LEADER_ENABLED,
    MW_PROBE_TIMEOUT,
)
from app.services.redis_store import redis_store

_LEADER_NAME = "leader:mq-worker"
_LEADER_TTL = 60
_LEADER_WAIT = 5.0
_IDLE_SLEEP = 0.5
_RECONNECT_MIN = 1.0
_RECONNECT_MAX = 30.0
_SANITIZE_MARKERS = ("__password__", "Bearer ", "Authorization", "ssh_key", "agent_token")


def _mask_password(text: str) -> str:
    """把 MQ_URL 内的密码从文本中抹掉（错误信息可能回显 URL）。"""
    if not text or not MQ_URL:
        return text
    try:
        password = urlsplit(MQ_URL).password
    except ValueError:
        password = None
    if password:
        text = text.replace(password, "***")
        decoded = unquote(password)
        if decoded != password:
            text = text.replace(decoded, "***")
    return text


def _sanitize(value: Any, limit: int = 500) -> str:
    text = _mask_password(str(value or ""))
    for marker in _SANITIZE_MARKERS:
        if marker in text:
            text = text.replace(marker, "***")
    return text[:limit]


def _open_connection(url: str):
    """构造 BlockingConnection（惰性；测试可 monkeypatch 本函数）。"""
    params = pika.URLParameters(url)
    params.heartbeat = 60
    params.blocked_connection_timeout = 10
    return pika.BlockingConnection(params)


class TaskQueue:
    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[dict], Any]] = {}
        self._handler_lock = threading.Lock()
        self._conn_lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._connection: Any = None
        self._channel: Any = None
        self._worker: threading.Thread | None = None
        self._worker_running = False
        self._leader = False
        self._stop_event = threading.Event()
        self._last_ok = False
        self._last_error = ""

    # ── 连接管理 ──

    def _close_transport_locked(self) -> None:
        channel, connection = self._channel, self._connection
        self._channel = None
        self._connection = None
        for obj in (channel, connection):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass

    def _reset_transport(self) -> None:
        with self._conn_lock:
            self._close_transport_locked()

    def _ensure_channel(self):
        with self._conn_lock:
            channel = self._channel
            if channel is not None and getattr(channel, "is_open", False):
                return channel
            self._close_transport_locked()
            if not MQ_URL:
                raise RuntimeError("MQ_URL 未配置")
            connection = _open_connection(MQ_URL)
            channel = connection.channel()
            channel.queue_declare(queue=MQ_DLQ, durable=True)
            channel.queue_declare(queue=MQ_QUEUE, durable=True, arguments={
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": MQ_DLQ,
            })
            self._connection = connection
            self._channel = channel
            self._last_ok = True
            self._last_error = ""
            return channel

    # ── 任务注册表（tasks.py，惰性 import 保持模块独立性） ──

    @staticmethod
    def _registry():
        try:
            from app.services.tasks import task_registry
            return task_registry
        except Exception:
            return None

    def _track_publish(self, task_id: str, task_type: str, server_id: str | None) -> None:
        registry = self._registry()
        if registry is None:
            return
        try:
            registry.create(task_id, task_type, server_id=server_id, meta={"queue": MQ_QUEUE})
        except Exception:
            pass

    def _track_running(self, task_id: Any) -> None:
        registry = self._registry()
        if registry is None or not task_id:
            return
        try:
            registry.update(str(task_id), status="running")
        except Exception:
            pass

    def _track_failure(self, task_id: Any, error: str) -> None:
        registry = self._registry()
        if registry is None or not task_id:
            return
        try:
            registry.finish(str(task_id), False, error=error)
        except Exception:
            pass

    # ── 状态 ──

    def _configured(self) -> bool:
        return bool(MQ_ENABLED and MQ_URL)

    def _endpoint(self) -> str:
        if not MQ_URL:
            return ""
        try:
            parts = urlsplit(MQ_URL)
            host = parts.hostname or ""
            port = parts.port or (5671 if parts.scheme == "amqps" else 5672)
        except ValueError:
            return ""
        return f"{host}:{port}" if host else ""

    def available(self) -> bool:
        try:
            return bool(MQ_ENABLED and MQ_URL and self._last_ok)
        except Exception:
            return False

    def status(self) -> dict:
        payload = {
            "kind": "rabbitmq",
            "configured": bool(MQ_URL),
            "available": False,
            "latency_ms": None,
            "error": "",
            "detail": {
                "endpoint": self._endpoint(),
                "queue": MQ_QUEUE,
                "dlq": MQ_DLQ,
                "worker_running": bool(self._worker_running),
                "leader": bool(self._leader),
            },
        }
        if MQ_ENABLED and MQ_URL:
            try:
                started = time.perf_counter()
                self._ensure_channel()
                payload["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
                self._last_ok = True
                self._last_error = ""
            except Exception as exc:
                self._last_ok = False
                self._last_error = _sanitize(exc)
        payload["available"] = self.available()
        payload["error"] = _sanitize(self._last_error)
        return payload

    # ── 发布 / 注册 ──

    def publish(self, task_type: str, payload: dict, *, server_id: str | None = None) -> dict | None:
        if not MQ_ENABLED or not MQ_URL:
            return None
        task_id = str(uuid.uuid4())
        message = {
            "task_id": task_id,
            "task_type": str(task_type),
            "server_id": str(server_id) if server_id else None,
            "payload": payload if isinstance(payload, dict) else {},
            "ts": time.time(),
        }
        try:
            body = json.dumps(message, ensure_ascii=False).encode("utf-8")
            properties = pika.BasicProperties(delivery_mode=2, content_type="application/json")
            with self._io_lock:
                channel = self._ensure_channel()
                channel.basic_publish(exchange="", routing_key=MQ_QUEUE, body=body,
                                      properties=properties)
            self._last_ok = True
            self._last_error = ""
        except Exception as exc:
            self._last_ok = False
            self._last_error = _sanitize(exc)
            self._reset_transport()
            return None
        self._track_publish(task_id, str(task_type), message["server_id"])
        return {"task_id": task_id, "queued": True, "queue": MQ_QUEUE}

    def register_handler(self, task_type: str, fn: Callable[[dict], Any]) -> None:
        try:
            with self._handler_lock:
                self._handlers[str(task_type)] = fn
        except Exception:
            pass

    # ── 消费 ──

    def _fetch(self):
        with self._io_lock:
            channel = self._ensure_channel()
            method, _properties, body = channel.basic_get(queue=MQ_QUEUE, auto_ack=False)
        if method is None:
            return None
        return channel, method, body

    def _nack(self, channel, method) -> None:
        try:
            with self._io_lock:
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        except Exception:
            pass

    def _dispatch(self, envelope) -> dict | None:
        channel, method, body = envelope
        try:
            raw = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError("消息不是 JSON 对象")
        except Exception:
            self._nack(channel, method)
            return None
        task_type = str(message.get("task_type") or "")
        with self._handler_lock:
            handler = self._handlers.get(task_type)
        if handler is None:
            self._nack(channel, method)
            return message
        task_id = message.get("task_id")
        self._track_running(task_id)
        try:
            handler(message)
        except Exception as exc:
            self._track_failure(task_id, _sanitize(exc))
            self._nack(channel, method)
            return message
        try:
            with self._io_lock:
                channel.basic_ack(delivery_tag=method.delivery_tag)
            self._last_ok = True
            self._last_error = ""
        except Exception as exc:
            self._last_ok = False
            self._last_error = _sanitize(exc)
        return message

    def consume_once(self, timeout: float = 1.0) -> dict | None:
        if not self._configured():
            return None
        try:
            deadline = time.monotonic() + max(0.0, float(timeout or 0.0))
            while True:
                envelope = self._fetch()
                if envelope is not None:
                    return self._dispatch(envelope)
                if time.monotonic() >= deadline:
                    return None
                time.sleep(0.05)
        except Exception as exc:
            self._last_ok = False
            self._last_error = _sanitize(exc)
            self._reset_transport()
            return None

    # ── worker ──

    def _gate_leader(self) -> bool:
        if not MW_LEADER_ENABLED:
            return True
        try:
            available = bool(redis_store.available())
        except Exception:
            available = False
        if not available:
            self._leader = False
            return True
        try:
            holding = bool(redis_store.leader(_LEADER_NAME, ttl=_LEADER_TTL))
        except Exception:
            holding = False
        self._leader = holding
        if not holding:
            self._stop_event.wait(_LEADER_WAIT)
            return False
        return True

    def _worker_loop(self) -> None:
        self._worker_running = True
        backoff = _RECONNECT_MIN
        try:
            while not self._stop_event.is_set():
                if not self._gate_leader():
                    continue
                try:
                    envelope = self._fetch()
                    if envelope is None:
                        backoff = _RECONNECT_MIN
                        self._stop_event.wait(_IDLE_SLEEP)
                        continue
                    self._dispatch(envelope)
                    backoff = _RECONNECT_MIN
                except Exception as exc:
                    self._last_ok = False
                    self._last_error = _sanitize(exc)
                    self._reset_transport()
                    self._stop_event.wait(backoff)
                    backoff = min(backoff * 2, _RECONNECT_MAX)
        finally:
            self._worker_running = False
            self._leader = False

    def start_worker(self) -> bool:
        try:
            if self._worker is not None and self._worker.is_alive():
                return True
            if not self._configured():
                return False
            self._stop_event.clear()
            self._worker = threading.Thread(target=self._worker_loop, name="mq-worker", daemon=True)
            self._worker.start()
            return True
        except Exception:
            return False

    def stop_worker(self, timeout: float = 3.0) -> None:
        try:
            self._stop_event.set()
            worker = self._worker
            if worker is not None and worker.is_alive() and worker is not threading.current_thread():
                try:
                    worker.join(max(0.1, float(timeout or 3.0)))
                except Exception:
                    pass
                if worker.is_alive():
                    return
            self._worker = None
            self._worker_running = False
            self._leader = False
            self._reset_transport()
        except Exception:
            pass

    # ── Management API ──

    def _basic_auth(self):
        if not MQ_URL:
            return None
        try:
            parts = urlsplit(MQ_URL)
            username = parts.username
            if not username:
                return None
            password = unquote(parts.password) if parts.password is not None else ""
        except ValueError:
            return None
        return (unquote(username), password)

    def _management_base(self) -> str:
        if MQ_MANAGEMENT_URL:
            base = MQ_MANAGEMENT_URL.strip().rstrip("/")
            if base and not base.startswith(("http://", "https://")):
                base = "http://" + base
            return base
        if not MQ_URL:
            return ""
        try:
            host = urlsplit(MQ_URL).hostname or ""
        except ValueError:
            return ""
        return f"http://{host}:15672" if host else ""

    def queues(self) -> list[dict]:
        try:
            base = self._management_base()
            if not base:
                return []
            response = httpx.get(
                base + "/api/queues",
                auth=self._basic_auth(),
                timeout=float(MW_PROBE_TIMEOUT or 4.0),
            )
            response.raise_for_status()
            data = response.json()
            items = data if isinstance(data, list) else []
            queues: list[dict] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                queues.append({
                    "name": item.get("name", ""),
                    "messages": item.get("messages", 0),
                    "messages_ready": item.get("messages_ready", 0),
                    "messages_unacknowledged": item.get("messages_unacknowledged", 0),
                    "consumers": item.get("consumers", 0),
                    "state": item.get("state", ""),
                })
            return queues
        except Exception:
            return []


task_queue = TaskQueue()
