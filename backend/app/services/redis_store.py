"""Redis 缓存/分布式协调客户端（v5.0.0）。

用途：
- 任务状态持久化（tasks.py 优先写 Redis）
- 分布式锁/租约（MQ worker 选主、周期任务互斥）
- 数据服务 Redis 页面：keyspace/keys/info 只读浏览

环境变量：REDIS_URL（redis://[:password@]host:port/db），未配置时 configured=False，
available()=False。所有方法 fail-open：出错返回空值/False，不抛异常、不打印凭证。

实现要求（BE-1）：
- 使用 requirements 中的 redis==5.2.1（同步客户端），模块级单例 redis_store。
- 惰性建连：首次调用才 redis.Redis.from_url(..., socket_timeout=MW_PROBE_TIMEOUT,
  socket_connect_timeout=MW_PROBE_TIMEOUT, decode_responses=True, health_check_interval=30)。
- status() 做 PING 探测并缓存 10 秒，返回：
  {"kind":"redis","configured":bool,"available":bool,"latency_ms":float|None,
   "error":str,"detail":{"endpoint":"host:port/db" or "","version":"x.y.z"|""}}
  endpoint 只保留 host:port（不含密码）。
- 锁/租约：SET key token NX PX ttl；续约需比对 token（Lua 或 GET+EXPIRE）。
  leader(name, ttl) 每次调用都尝试 acquire-or-renew，返回 bool；token 为进程级随机串。
- scan/keys 浏览接口必须限制 count<=500、返回的 key 需带 type/ttl。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import unquote, urlsplit

import redis

from app.config import MW_ENABLED, MW_PROBE_TIMEOUT, REDIS_URL

_STATUS_TTL = 10.0          # status() 探测结果缓存秒数
_KEY_PREFIX = "opscenter:"  # 锁/租约 key 统一前缀
# 释放锁：仅当值等于持有者 token 才删除，避免误删他人锁（Lua 原子）。
_RELEASE_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "return redis.call('DEL', KEYS[1]) end return 0"
)
# 本进程选主 token（模块级；测试可替换）
_TOKEN = uuid.uuid4().hex


def _endpoint() -> str:
    """从 REDIS_URL 提取 host:port/db；无法解析返回空串。绝不包含密码。"""
    if not REDIS_URL:
        return ""
    try:
        parts = urlsplit(REDIS_URL)
        host = parts.hostname or ""
        port = parts.port or 6379
    except ValueError:
        return ""
    if not host:
        return ""
    db = (parts.path or "").strip("/") or "0"
    return f"{host}:{port}/{db}"


def _mask_error(exc: Any) -> str:
    """错误信息脱敏：将 REDIS_URL 中的密码替换为 ***。"""
    message = str(exc or "")
    if not message or not REDIS_URL:
        return message
    try:
        password = urlsplit(REDIS_URL).password
    except ValueError:
        password = None
    if password:
        message = message.replace(password, "***")
        decoded = unquote(password)
        if decoded != password:
            message = message.replace(decoded, "***")
    return message


class RedisStore:
    def __init__(self) -> None:
        self._client: Any = None
        self._client_lock = threading.Lock()
        self._status_cache: dict | None = None
        self._status_at = 0.0

    # ── 内部辅助 ──

    def _configured(self) -> bool:
        return bool(MW_ENABLED) and bool(REDIS_URL)

    def _ensure_client(self) -> Any:
        """惰性构造客户端；未配置返回 None。from_url 仅构造参数，不产生网络 I/O。"""
        if not self._configured():
            return None
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = redis.Redis.from_url(
                        REDIS_URL,
                        decode_responses=True,
                        socket_timeout=MW_PROBE_TIMEOUT,
                        socket_connect_timeout=MW_PROBE_TIMEOUT,
                        health_check_interval=30,
                    )
        return self._client

    @staticmethod
    def _version(client: Any) -> str:
        try:
            data = client.info("server") or {}
            return str(data.get("redis_version", ""))
        except Exception:
            return ""

    def _shell(self) -> dict:
        return {
            "kind": "redis",
            "configured": self._configured(),
            "available": False,
            "latency_ms": None,
            "error": "",
            "detail": {"endpoint": _endpoint(), "version": ""},
        }

    # ── 状态 ──

    def available(self) -> bool:
        """最近一次探测是否可用（未配置/不可达=False）；带 10s 缓存。"""
        return self._configured() and bool(self.status().get("available"))

    def status(self) -> dict:
        """标准化状态（见模块 docstring）；永不抛异常。"""
        if self._status_cache is not None and (time.monotonic() - self._status_at) < _STATUS_TTL:
            return self._status_cache
        status = self._shell()
        if status["configured"]:
            try:
                client = self._ensure_client()
                started = time.perf_counter()
                client.ping()
                status["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
                status["available"] = True
                status["detail"]["version"] = self._version(client)
            except Exception as exc:
                status["error"] = _mask_error(exc)
        self._status_cache = status
        self._status_at = time.monotonic()
        return status

    # ── 基础 KV ──

    def get(self, key: str) -> str | None:
        try:
            client = self._ensure_client()
            return client.get(key) if client is not None else None
        except Exception:
            return None

    def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        try:
            client = self._ensure_client()
            if client is None:
                return False
            if ttl:
                return bool(client.set(key, value, ex=int(ttl)))
            return bool(client.set(key, value))
        except Exception:
            return False

    def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        try:
            client = self._ensure_client()
            if client is None:
                return 0
            return int(client.delete(*keys) or 0)
        except Exception:
            return 0

    def get_json(self, key: str) -> Any | None:
        raw = self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    def set_json(self, key: str, value: Any, ttl: int | None = None) -> bool:
        try:
            payload = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return False
        return self.set(key, payload, ttl)

    def incr(self, key: str, ttl: int | None = None) -> int | None:
        """INCR；ttl 仅在 key 新建时设置（限流窗口用）。"""
        try:
            client = self._ensure_client()
            if client is None:
                return None
            value = int(client.incr(key))
            if ttl and value == 1:
                client.expire(key, int(ttl))
            return value
        except Exception:
            return None

    # ── 锁 / 租约 ──

    @contextmanager
    def lock(self, name: str, ttl: int = 30, blocking_timeout: float = 0.0) -> Iterator[bool]:
        """分布式锁：yield True=拿到锁，False=没拿到。退出时仅持锁者释放。"""
        key = f"{_KEY_PREFIX}{name}"
        token = uuid.uuid4().hex
        ttl_ms = max(1, int(ttl)) * 1000
        acquired = False
        try:
            client = self._ensure_client()
        except Exception:
            client = None
        if client is not None:
            try:
                acquired = bool(client.set(key, token, nx=True, px=ttl_ms))
                deadline = time.monotonic() + max(0.0, blocking_timeout)
                while not acquired and blocking_timeout > 0 and time.monotonic() < deadline:
                    time.sleep(0.1)
                    acquired = bool(client.set(key, token, nx=True, px=ttl_ms))
            except Exception:
                acquired = False
        try:
            yield acquired
        finally:
            if acquired and client is not None:
                try:
                    client.eval(_RELEASE_LUA, 1, key, token)
                except Exception:
                    try:
                        if client.get(key) == token:
                            client.delete(key)
                    except Exception:
                        pass

    def leader(self, name: str, ttl: int = 60) -> bool:
        """租约选主：acquire-or-renew，True=本进程当前持有。"""
        key = f"{_KEY_PREFIX}{name}"
        ttl_ms = max(1, int(ttl)) * 1000
        try:
            client = self._ensure_client()
            if client is None:
                return False
            if client.get(key) == _TOKEN:
                client.expire(key, max(1, int(ttl)))
                return True
            return bool(client.set(key, _TOKEN, nx=True, px=ttl_ms))
        except Exception:
            return False

    # ── 只读浏览 ──

    def info(self, section: str | None = None) -> dict:
        try:
            client = self._ensure_client()
            if client is None:
                return {}
            data = client.info(section) if section else client.info()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def dbsize(self) -> int:
        try:
            client = self._ensure_client()
            if client is None:
                return 0
            return int(client.dbsize() or 0)
        except Exception:
            return 0

    def scan(self, cursor: int = 0, pattern: str = "*", count: int = 100) -> tuple[int, list[str]]:
        """SCAN 包装；失败返回 (0, [])。count 上限 500。"""
        limit = max(1, min(int(count), 500))
        try:
            client = self._ensure_client()
            if client is None:
                return (0, [])
            next_cursor, keys = client.scan(cursor=int(cursor), match=pattern or "*", count=limit)
            return (int(next_cursor), list(keys))
        except Exception:
            return (0, [])

    def key_meta(self, key: str) -> dict:
        """{"key":k,"type":t,"ttl":-1}；失败返回空 dict。"""
        try:
            client = self._ensure_client()
            if client is None:
                return {}
            pipe = client.pipeline(transaction=False)
            pipe.type(key)
            pipe.ttl(key)
            kind, ttl = pipe.execute()
            return {"key": key, "type": kind, "ttl": int(ttl)}
        except Exception:
            return {}

    def raw(self) -> Any:
        """底层 redis.Redis | None（只读页面兜底用，不暴露给前端）。"""
        try:
            return self._ensure_client()
        except Exception:
            return None


redis_store = RedisStore()
