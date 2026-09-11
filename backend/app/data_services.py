"""数据服务 API（v5.0.0）—— K3s middleware ns 七件套纳管与只读浏览。

契约（冻结，前端 frontend-vite/src/api/dataservices.js 依赖）：
- 全部端点 HTTP 200 + 统一信封 {data, data_timestamp, cached, cache_age_seconds, partial_errors, source_status}；
- 中间件未配置/不可达时 data 内可用字段为空并置 available=false / partial_errors，绝不 5xx；
- 全部端点鉴权 Depends(require_operator)（AUTH_ENABLED 或 OPERATOR_TOKEN 生效）；
- GET /overview 以 ThreadPoolExecutor(7) 并行探测，总超时 8s，超时卡片 error="probe timeout"；
- 端点清单：
  GET  /overview                     七件套卡片（状态/延迟/摘要）
  GET  /{kind}/status                单件状态
  POST /{kind}/probe                 主动连通性探测（清状态缓存后强制刷新）
  GET  /redis/keyspace               dbsize/keyspace/memory
  GET  /redis/keys?pattern=&cursor=&count=
  GET  /rabbitmq/queues
  GET  /kafka/topics
  GET  /kafka/consumer-groups
  GET  /zookeeper/tree?path=&depth=
  GET  /nacos/services
  GET  /nacos/configs?group=&page=&size=
  GET  /minio/buckets
  GET  /minio/objects?bucket=&prefix=&limit=
  GET  /mongodb/databases
  GET  /mongodb/collections?db=&limit=
"""

from __future__ import annotations

import time
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_operator
from app.config import MW_PROBE_TIMEOUT
from app.services.doc_store import doc_store
from app.services.event_bus import event_bus
from app.services.mq import task_queue
from app.services.nacos_config import nacos_config
from app.services.object_store import object_store
from app.services.redis_store import redis_store
from app.services.zk_view import zk_view

router = APIRouter(prefix="/api/v2/data-services", tags=["data-services"])

_KINDS: tuple[str, ...] = ("redis", "rabbitmq", "kafka", "zookeeper", "nacos", "minio", "mongodb")
_NAMES = {
    "redis": "Redis",
    "rabbitmq": "RabbitMQ",
    "kafka": "Kafka",
    "zookeeper": "ZooKeeper",
    "nacos": "Nacos",
    "minio": "MinIO",
    "mongodb": "MongoDB",
}
# 总预算 8s 为需求基线；单件 status() 自身应受 MW_PROBE_TIMEOUT 约束，+2s 余量仍未返回视作挂死。
_OVERVIEW_TIMEOUT = 8.0
_PER_KIND_TIMEOUT = max(1.0, float(MW_PROBE_TIMEOUT or 0.0)) + 2.0
_REDIS_KEYS_MAX_COUNT = 200
_PROBE_EXECUTOR = ThreadPoolExecutor(max_workers=len(_KINDS), thread_name_prefix="ds-probe")
_PROBE_SLOTS = threading.BoundedSemaphore(len(_KINDS) * 2)


# ── 基础工具 ──────────────────────────────────────────────────


def _service_for(kind: str):
    """按 kind 解析单例（每次调用读取模块级绑定，便于测试 monkeypatch）。"""
    return {
        "redis": redis_store,
        "rabbitmq": task_queue,
        "kafka": event_bus,
        "zookeeper": zk_view,
        "nacos": nacos_config,
        "minio": object_store,
        "mongodb": doc_store,
    }.get(kind)


def _envelope(data: Any, *, partial_errors: list | None = None,
              source_status: dict | None = None) -> dict:
    return {
        "data": data,
        "data_timestamp": int(time.time()),
        "cached": False,
        "cache_age_seconds": 0,
        "partial_errors": list(partial_errors or []),
        "source_status": dict(source_status or {}),
    }


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def _pick(mapping: dict, *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _kind_available(kind: str) -> bool:
    obj = _service_for(kind)
    if obj is None:
        return False
    try:
        return bool(obj.available())
    except Exception:
        return False


def _card(kind: str, status: Any) -> dict:
    """把底层 status() 字典归一化为 overview/{kind}/status 的卡片。"""
    st = status if isinstance(status, dict) else {}
    detail = st.get("detail") if isinstance(st.get("detail"), dict) else {}
    endpoint = ""
    for key in ("endpoint", "endpoints", "bootstrap"):
        if detail.get(key):
            endpoint = str(detail[key])
            break
    summary = {k: v for k, v in detail.items()
               if k not in ("endpoint", "endpoints", "bootstrap", "version")}
    return {
        "kind": kind,
        "name": _NAMES.get(kind, kind),
        "configured": bool(st.get("configured", False)),
        "available": bool(st.get("available", False)),
        "latency_ms": st.get("latency_ms"),
        "endpoint": endpoint,
        "version": str(detail.get("version") or ""),
        "summary": summary,
        "error": str(st.get("error") or ""),
    }


def _unavailable_card(kind: str, error: str) -> dict:
    card = _card(kind, {})
    card["error"] = str(error)
    return card


def _card_partial_errors(card: dict) -> list[str]:
    if not card.get("available") and card.get("error"):
        return [f"{card['kind']}: {card['error']}"]
    return []


def _probe_kind(kind: str) -> dict:
    """单件探测（overview 线程池任务）：status() 异常 → 不可用卡片，不外抛。"""
    obj = _service_for(kind)
    if obj is None:
        return _unavailable_card(kind, "unknown kind")
    try:
        return _card(kind, obj.status())
    except Exception as exc:
        return _unavailable_card(kind, str(exc))


def _submit_probe(kind: str):
    if not _PROBE_SLOTS.acquire(blocking=False):
        return None
    future = _PROBE_EXECUTOR.submit(_probe_kind, kind)
    future.add_done_callback(lambda _future: _PROBE_SLOTS.release())
    return future


# ── 七件套总览 / 单件状态 / 主动探测 ──────────────────────────


@router.get("/overview")
def data_services_overview(_operator: object = Depends(require_operator)) -> dict:
    cards: list[dict] = []
    futures = {kind: _submit_probe(kind) for kind in _KINDS}
    deadline = time.monotonic() + _OVERVIEW_TIMEOUT
    for kind in _KINDS:
        future = futures[kind]
        if future is None:
            cards.append(_unavailable_card(kind, "probe busy"))
            continue
        wait = max(0.0, min(deadline - time.monotonic(), _PER_KIND_TIMEOUT))
        try:
            cards.append(future.result(timeout=wait))
        except FutureTimeoutError:
            future.cancel()
            cards.append(_unavailable_card(kind, "probe timeout"))
        except Exception as exc:
            cards.append(_unavailable_card(kind, str(exc)))
    errors: list[str] = []
    for card in cards:
        errors.extend(_card_partial_errors(card))
    source_status = {card["kind"]: card["available"] for card in cards}
    return _envelope({"services": cards}, partial_errors=errors, source_status=source_status)


@router.get("/{kind}/status")
def data_service_status(kind: str, _operator: object = Depends(require_operator)) -> dict:
    obj = _service_for(kind)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"未知数据服务: {kind}")
    try:
        card = _card(kind, obj.status())
    except Exception as exc:
        card = _unavailable_card(kind, str(exc))
    return _envelope(card, partial_errors=_card_partial_errors(card),
                     source_status={kind: card["available"]})


@router.post("/{kind}/probe")
def data_service_probe(kind: str, _operator: object = Depends(require_operator)) -> dict:
    obj = _service_for(kind)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"未知数据服务: {kind}")
    started = time.monotonic()
    available = False
    latency_ms = None
    error = ""
    try:
        # 防御性清状态缓存（若有），强制本次 status() 重新探测
        if hasattr(obj, "_status_cache"):
            try:
                setattr(obj, "_status_cache", None)
            except Exception:
                pass
        st = obj.status()
        st = st if isinstance(st, dict) else {}
        available = bool(st.get("available", False))
        latency_ms = st.get("latency_ms")
        error = str(st.get("error") or "")
    except Exception as exc:
        error = str(exc)
    if latency_ms is None:
        latency_ms = round((time.monotonic() - started) * 1000.0, 2)
    data = {"kind": kind, "available": available, "latency_ms": latency_ms, "error": error}
    partial = [f"{kind}: {error}"] if (not available and error) else []
    return _envelope(data, partial_errors=partial, source_status={kind: available})


# ── Redis ────────────────────────────────────────────────────


@router.get("/redis/keyspace")
def redis_keyspace(_operator: object = Depends(require_operator)) -> dict:
    errors: list[str] = []
    available = _kind_available("redis")
    dbsize = 0
    keyspace: dict = {}
    memory = {"used": 0, "max": 0, "policy": ""}
    try:
        dbsize = _as_int(redis_store.dbsize())
    except Exception as exc:
        errors.append(f"redis: {exc}")
        available = False
    try:
        info = redis_store.info()
        if not isinstance(info, dict):
            info = {}
        ks = info.get("keyspace")
        keyspace = ks if isinstance(ks, dict) else {}
        memory = {
            "used": _as_int(_pick(info, "used_memory", "memory_used")),
            "max": _as_int(_pick(info, "maxmemory", "memory_max")),
            "policy": str(_pick(info, "maxmemory_policy", "memory_policy") or ""),
        }
    except Exception as exc:
        errors.append(f"redis: {exc}")
        available = False
    data = {"dbsize": dbsize, "keyspace": keyspace, "memory": memory, "available": available}
    return _envelope(data, partial_errors=errors, source_status={"redis": available})


@router.get("/redis/keys")
def redis_keys(pattern: str = "*", cursor: int = 0, count: int = 100,
               _operator: object = Depends(require_operator)) -> dict:
    errors: list[str] = []
    available = _kind_available("redis")
    cursor_out = max(0, _as_int(cursor))
    keys: list[dict] = []
    bounded = max(1, min(_as_int(count, 100), _REDIS_KEYS_MAX_COUNT))
    try:
        cursor_out, raw_keys = redis_store.scan(cursor_out, pattern or "*", bounded)
        cursor_out = _as_int(cursor_out)
        for key in list(raw_keys or []):
            meta = redis_store.key_meta(key)
            row = {"key": key, "type": "", "ttl": -1}
            if isinstance(meta, dict):
                for field in ("key", "type", "ttl"):
                    if field in meta:
                        row[field] = meta[field]
            row["key"] = row["key"] or key
            keys.append(row)
    except Exception as exc:
        errors.append(f"redis: {exc}")
        available = False
        cursor_out, keys = 0, []
    data = {"cursor": cursor_out, "keys": keys, "available": available}
    return _envelope(data, partial_errors=errors, source_status={"redis": available})


# ── RabbitMQ ─────────────────────────────────────────────────


@router.get("/rabbitmq/queues")
def rabbitmq_queues(_operator: object = Depends(require_operator)) -> dict:
    errors: list[str] = []
    available = _kind_available("rabbitmq")
    try:
        queues = task_queue.queues()
    except Exception as exc:
        errors.append(f"rabbitmq: {exc}")
        queues = []
        available = False
    if not isinstance(queues, list):
        queues = []
    data = {"queues": queues, "available": available}
    return _envelope(data, partial_errors=errors, source_status={"rabbitmq": available})


# ── Kafka ────────────────────────────────────────────────────


@router.get("/kafka/topics")
def kafka_topics(_operator: object = Depends(require_operator)) -> dict:
    errors: list[str] = []
    available = _kind_available("kafka")
    try:
        topics = event_bus.topics()
    except Exception as exc:
        errors.append(f"kafka: {exc}")
        topics = []
        available = False
    if not isinstance(topics, list):
        topics = []
    data = {"topics": topics, "available": available}
    return _envelope(data, partial_errors=errors, source_status={"kafka": available})


@router.get("/kafka/consumer-groups")
def kafka_consumer_groups(_operator: object = Depends(require_operator)) -> dict:
    errors: list[str] = []
    available = _kind_available("kafka")
    try:
        groups = event_bus.consumer_groups()
    except Exception as exc:
        errors.append(f"kafka: {exc}")
        groups = []
        available = False
    if not isinstance(groups, list):
        groups = []
    data = {"groups": groups, "available": available}
    return _envelope(data, partial_errors=errors, source_status={"kafka": available})


# ── ZooKeeper ────────────────────────────────────────────────


@router.get("/zookeeper/tree")
def zookeeper_tree(path: str = "/", depth: int = 2,
                   _operator: object = Depends(require_operator)) -> dict:
    errors: list[str] = []
    available = _kind_available("zookeeper")
    try:
        tree = zk_view.tree(path or "/", max(0, _as_int(depth, 2)))
    except Exception as exc:
        errors.append(f"zookeeper: {exc}")
        tree = None
        available = False
    if not isinstance(tree, dict):
        tree = {}
    data = dict(tree)
    data.setdefault("path", path or "/")
    data.setdefault("children", [])
    data.setdefault("truncated", False)
    data["available"] = available
    return _envelope(data, partial_errors=errors, source_status={"zookeeper": available})


# ── Nacos ────────────────────────────────────────────────────


@router.get("/nacos/services")
def nacos_services(_operator: object = Depends(require_operator)) -> dict:
    errors: list[str] = []
    available = _kind_available("nacos")
    try:
        services = nacos_config.list_services()
    except Exception as exc:
        errors.append(f"nacos: {exc}")
        services = []
        available = False
    if not isinstance(services, list):
        services = []
    data = {"services": services, "available": available}
    return _envelope(data, partial_errors=errors, source_status={"nacos": available})


@router.get("/nacos/configs")
def nacos_configs(group: str = "", page: int = 1, size: int = 50,
                  _operator: object = Depends(require_operator)) -> dict:
    errors: list[str] = []
    available = _kind_available("nacos")
    try:
        result = nacos_config.list_configs(
            group=(group or None), page=max(1, _as_int(page, 1)), size=max(1, _as_int(size, 50)))
    except Exception as exc:
        errors.append(f"nacos: {exc}")
        result = None
        available = False
    if not isinstance(result, dict):
        result = {}
    data = {
        "total": _as_int(result.get("total")),
        "configs": result.get("configs") if isinstance(result.get("configs"), list) else [],
        "available": available,
    }
    return _envelope(data, partial_errors=errors, source_status={"nacos": available})


# ── MinIO ────────────────────────────────────────────────────


@router.get("/minio/buckets")
def minio_buckets(_operator: object = Depends(require_operator)) -> dict:
    errors: list[str] = []
    available = _kind_available("minio")
    try:
        buckets = object_store.list_buckets()
    except Exception as exc:
        errors.append(f"minio: {exc}")
        buckets = []
        available = False
    if not isinstance(buckets, list):
        buckets = []
    data = {"buckets": buckets, "available": available}
    return _envelope(data, partial_errors=errors, source_status={"minio": available})


@router.get("/minio/objects")
def minio_objects(bucket: str = "", prefix: str = "", limit: int = 100,
                  _operator: object = Depends(require_operator)) -> dict:
    errors: list[str] = []
    available = _kind_available("minio")
    try:
        objects = object_store.list_objects(prefix=prefix or "", bucket=(bucket or None),
                                            limit=max(1, _as_int(limit, 100)))
    except Exception as exc:
        errors.append(f"minio: {exc}")
        objects = []
        available = False
    if not isinstance(objects, list):
        objects = []
    data = {"objects": objects, "available": available}
    return _envelope(data, partial_errors=errors, source_status={"minio": available})


# ── MongoDB ──────────────────────────────────────────────────


@router.get("/mongodb/databases")
def mongodb_databases(_operator: object = Depends(require_operator)) -> dict:
    errors: list[str] = []
    available = _kind_available("mongodb")
    try:
        databases = doc_store.list_databases()
    except Exception as exc:
        errors.append(f"mongodb: {exc}")
        databases = []
        available = False
    if not isinstance(databases, list):
        databases = []
    data = {"databases": databases, "available": available}
    return _envelope(data, partial_errors=errors, source_status={"mongodb": available})


@router.get("/mongodb/collections")
def mongodb_collections(db: str = "", limit: int = 100,
                        _operator: object = Depends(require_operator)) -> dict:
    errors: list[str] = []
    available = _kind_available("mongodb")
    try:
        collections = doc_store.list_collections(db=(db or None), limit=max(1, _as_int(limit, 100)))
    except Exception as exc:
        errors.append(f"mongodb: {exc}")
        collections = []
        available = False
    if not isinstance(collections, list):
        collections = []
    data = {"collections": collections, "available": available}
    return _envelope(data, partial_errors=errors, source_status={"mongodb": available})
