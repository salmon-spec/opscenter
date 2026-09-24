"""Data-service monitoring and multidimensional metric history (v5.2.2).

The existing `/api/v2/data-services` read-only routes stay in place.  This
module adds a normalized metric contract, live collectors, bounded history and
time-series queries on top of the same middleware clients.
"""

from __future__ import annotations

import asyncio
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, text

from app.auth import require_configured_operator
from app import data_service_instances as instances
from app.config import (
    MW_METRICS_ENABLED,
    MW_METRICS_INTERVAL_SECONDS,
    MW_METRICS_RETENTION_DAYS,
)
from app.database import get_db
from app.models import MiddlewareMetricSample
from app.services.doc_store import doc_store
from app.services.event_bus import event_bus
from app.services.mq import task_queue
from app.services.nacos_config import nacos_config
from app.services.object_store import object_store
from app.services.redis_store import redis_store
from app.services.zk_view import zk_view

router = APIRouter(prefix="/api/v2/data-services", tags=["data-service-monitor"])

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
_DIMENSION_LABELS = {
    "instance": "实例",
    "node": "节点",
    "role": "角色",
    "resource": "资源",
    "database": "数据库",
    "collection": "集合",
    "bucket": "Bucket",
    "topic": "Topic",
    "partition": "分区",
    "group": "消费组",
    "queue": "队列",
    "command": "命令",
    "path": "路径",
    "service": "服务",
    "namespace": "命名空间",
}
_THRESHOLDS: dict[str, tuple[float, float, str]] = {
    "data_service_available": (1.0, 1.0, "lower"),
    "data_service_latency_ms": (500.0, 2000.0, "higher"),
    "redis_memory_usage_percent": (80.0, 95.0, "higher"),
    "redis_memory_fragmentation_ratio": (1.5, 2.0, "higher"),
    "redis_evicted_keys_total": (1.0, 100.0, "higher"),
    "redis_rejected_connections_total": (1.0, 10.0, "higher"),
    "rabbitmq_messages_total": (1000.0, 10000.0, "higher"),
    "rabbitmq_messages_ready": (1000.0, 10000.0, "higher"),
    "rabbitmq_oldest_message_age_seconds": (300.0, 3600.0, "higher"),
    "rabbitmq_queue_oldest_message_age_seconds": (300.0, 3600.0, "higher"),
    "rabbitmq_disk_free_alarm": (1.0, 1.0, "bool_bad"),
    "rabbitmq_memory_alarm": (1.0, 1.0, "bool_bad"),
    "rabbitmq_partitions_total": (1.0, 1.0, "bool_bad"),
    "kafka_under_replicated_partitions": (1.0, 10.0, "higher"),
    "kafka_offline_partitions": (1.0, 1.0, "higher"),
    "kafka_consumer_lag_total": (1000.0, 10000.0, "higher"),
    "kafka_partition_consumer_lag": (1000.0, 10000.0, "higher"),
    "zookeeper_outstanding_requests": (10.0, 100.0, "higher"),
    "zookeeper_znode_count": (100000.0, 500000.0, "higher"),
    "nacos_unhealthy_instances": (1.0, 10.0, "higher"),
    "minio_object_count_sampled": (1000000.0, 10000000.0, "higher"),
    "mongodb_replication_lag_seconds": (10.0, 60.0, "higher"),
    "mongodb_current_connections": (500.0, 1000.0, "higher"),
}

_CACHE_TTL = 30.0
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, dict]] = {}
_EXECUTOR = ThreadPoolExecutor(max_workers=max(8, len(_KINDS) * 2), thread_name_prefix="ds-monitor")
_OVERVIEW_TIMEOUT = 8.0
_SAMPLE_MAX_METRICS_PER_KIND = 250
_TIMESERIES_MAX_ROWS = 30000
_SAMPLE_LOCK = threading.Lock()
_SAMPLE_DB_LOCK_ID = 0x4F505343
_THRESHOLD_ALIASES = {
    "rabbitmq_queue_oldest_message_age_seconds": "rabbitmq_oldest_message_age_seconds",
}


def _envelope(data: Any, *, partial_errors: list[str] | None = None,
              source_status: dict | None = None) -> dict:
    return {
        "data": data,
        "data_timestamp": int(time.time()),
        "cached": False,
        "cache_age_seconds": 0,
        "partial_errors": list(partial_errors or []),
        "source_status": dict(source_status or {}),
    }


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _dimension_key(dimensions: dict | None) -> str:
    clean = {
        str(key): str(value)
        for key, value in (dimensions or {}).items()
        if value is not None and str(value) != ""
    }
    if not clean:
        return ""
    return json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:512]


def _status_for_metric(key: str, value: Any, explicit: str = "") -> str:
    if explicit in {"ok", "warning", "critical", "unknown"}:
        return explicit
    number = _as_number(value)
    rule = _THRESHOLDS.get(key) or _THRESHOLDS.get(_THRESHOLD_ALIASES.get(key, ""))
    if number is None or not rule:
        return "ok"
    warn, critical, direction = rule
    if direction == "bool_bad":
        return "critical" if number >= critical else "ok"
    if direction == "bool_good":
        return "ok" if number >= critical else "critical"
    if direction == "lower":
        if number < critical:
            return "critical"
        if number < warn:
            return "warning"
        return "ok"
    if number >= critical:
        return "critical"
    if number >= warn:
        return "warning"
    return "ok"


def _normalize_metric(raw: dict, kind: str) -> dict | None:
    if not isinstance(raw, dict):
        return None
    key = str(raw.get("key") or raw.get("metric_key") or "").strip()
    if not key:
        return None
    value = raw.get("value")
    number = _as_number(value)
    dimensions = raw.get("dimensions")
    if not isinstance(dimensions, dict):
        dimensions = {}
    dimensions = {
        str(name): str(item)
        for name, item in dimensions.items()
        if item is not None and str(item) != ""
    }
    unit = str(raw.get("unit") or "count")
    label = str(raw.get("label") or key)
    return {
        "key": key,
        "label": label,
        "value": number if number is not None else value,
        "display_value": str(raw.get("display_value") or ""),
        "unit": unit,
        "numeric": number is not None,
        "status": _status_for_metric(key, number if number is not None else value,
                                     str(raw.get("status") or "")),
        "dimensions": dimensions,
        "dimension_key": _dimension_key(dimensions),
        "source": str(raw.get("source") or ""),
        "kind": kind,
    }


def _metric_priority(metric: dict) -> tuple[int, int, str]:
    """Keep abnormal, thresholded and aggregate metrics before detail rows."""
    key = str(metric.get("key") or "")
    status = str(metric.get("status") or "")
    thresholded = key in _THRESHOLDS or key in _THRESHOLD_ALIASES
    if thresholded and status in {"critical", "warning"}:
        priority = 0
    elif thresholded:
        priority = 1
    elif key.startswith("data_service_"):
        priority = 2
    elif key.endswith("_total") or "_total_" in key:
        priority = 3
    elif status in {"critical", "warning"}:
        priority = 4
    else:
        priority = 5
    return priority, 0 if metric.get("numeric") else 1, key


def _service_for(kind: str):
    return {
        "redis": redis_store,
        "rabbitmq": task_queue,
        "kafka": event_bus,
        "zookeeper": zk_view,
        "nacos": nacos_config,
        "minio": object_store,
        "mongodb": doc_store,
    }.get(kind)


def _status_card(kind: str, status: Any) -> dict:
    data = status if isinstance(status, dict) else {}
    detail = data.get("detail") if isinstance(data.get("detail"), dict) else {}
    endpoint = ""
    for key in ("endpoint", "endpoints", "bootstrap"):
        if detail.get(key):
            endpoint = str(detail[key])
            break
    return {
        "configured": bool(data.get("configured", False)),
        "available": bool(data.get("available", False)),
        "latency_ms": _as_number(data.get("latency_ms")),
        "error": str(data.get("error") or ""),
        "version": str(detail.get("version") or ""),
        "endpoint": endpoint,
    }


def _fallback_metrics(kind: str, card: dict, meta: dict | None = None) -> list[dict]:
    dims = {"instance": card["endpoint"] or kind}
    if meta and not meta.get("is_primary"):
        dims.update({"instance": str(meta.get("name") or meta.get("id")),
                    "instance_id": str(meta.get("id"))})
    rows = [
        {"key": "data_service_configured", "label": "已配置", "value": 1 if card["configured"] else 0,
         "unit": "bool", "dimensions": dict(dims), "source": "status"},
        {"key": "data_service_available", "label": "可用状态", "value": 1 if card["available"] else 0,
         "unit": "bool", "dimensions": dict(dims), "source": "status"},
    ]
    if card["latency_ms"] is not None:
        rows.append({
            "key": "data_service_latency_ms", "label": "探测延迟",
            "value": card["latency_ms"], "unit": "ms",
            "dimensions": dict(dims), "source": "status",
        })
    return rows


def _collector_result(value: Any) -> tuple[list[dict], list[str]]:
    if isinstance(value, dict):
        metrics = value.get("metrics")
        errors = value.get("errors")
        return (
            [item for item in metrics if isinstance(item, dict)] if isinstance(metrics, list) else [],
            [str(item) for item in errors if item is not None] if isinstance(errors, list) else [],
        )
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)], []
    if value is None:
        return [], []
    return [], ["monitor metrics returned an invalid payload"]


def _target_meta(kind: str, meta: dict | None) -> dict[str, Any]:
    value = dict(meta or {})
    return {
        "id": str(value.get("id") or "primary"),
        "name": str(value.get("name") or f"{_NAMES.get(kind, kind)}(默认)"),
        "is_primary": bool(value.get("is_primary", True)),
        "monitor_enabled": bool(value.get("monitor_enabled", True)),
    }


def _finalize_collection(
    kind: str,
    card: dict,
    raw_metrics: list[dict],
    errors: list[str],
    meta: dict,
    now: float,
) -> dict:
    metrics: list[dict] = []
    for item in raw_metrics:
        normalized = _normalize_metric(item, kind)
        if normalized:
            metrics.append(normalized)
    seen = {(metric["key"], metric["dimension_key"]) for metric in metrics}
    for fallback in _fallback_metrics(kind, card, meta):
        normalized = _normalize_metric(fallback, kind)
        if normalized and (normalized["key"], normalized["dimension_key"]) not in seen:
            metrics.append(normalized)
            seen.add((normalized["key"], normalized["dimension_key"]))

    metrics_total = len(metrics)
    metrics.sort(key=_metric_priority)
    metrics_truncated = metrics_total > _SAMPLE_MAX_METRICS_PER_KIND
    metrics = metrics[:_SAMPLE_MAX_METRICS_PER_KIND]

    dimensions: dict[str, set[str]] = {}
    for metric in metrics:
        for name, value in metric["dimensions"].items():
            dimensions.setdefault(name, set()).add(str(value))

    result = {
        "kind": kind,
        "name": _NAMES.get(kind, kind),
        "instance_id": meta["id"],
        "instance_name": meta["name"],
        "is_primary": meta["is_primary"],
        "configured": card["configured"],
        "available": card["available"],
        "latency_ms": card["latency_ms"],
        "version": card["version"],
        "endpoint": card["endpoint"],
        "error": card["error"],
        "metrics": metrics,
        "metrics_total": metrics_total,
        "metrics_truncated": metrics_truncated,
        "quality": "unknown" if errors else "ok",
        "dimensions": [
            {
                "key": name,
                "label": _DIMENSION_LABELS.get(name, name),
                "values": sorted(values)[:200],
                "count": len(values),
            }
            for name, values in sorted(dimensions.items())
        ],
        "partial_errors": errors,
        "source_status": {kind: card["available"]},
        "collected_at": int(time.time()),
        "cached": False,
        "cache_age_seconds": 0,
    }
    return result


def _collect_kind(kind: str, *, force: bool = False,
                  instance_id: str | None = None) -> dict:
    target_id = str(instance_id or "primary").strip() or "primary"
    cache_key = f"{kind}:{target_id}"
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and not force and now - cached[0] < _CACHE_TTL:
            result = dict(cached[1])
            result["cached"] = True
            result["cache_age_seconds"] = round(now - cached[0], 2)
            return result

    if target_id == "primary":
        meta = _target_meta(kind, None)
        service = _service_for(kind)
        errors: list[str] = []
        status: dict = {}
        raw_metrics: list[dict] = []
        if service is None:
            errors.append(f"{kind}: unknown service")
            status = {"configured": False, "available": False,
                      "error": "unknown service", "detail": {}}
        else:
            try:
                status = service.status()
            except Exception as exc:
                errors.append(f"{kind}: {exc}")
                status = {"configured": False, "available": False,
                          "error": str(exc), "detail": {}}
            collector: Callable[[], Any] | None = getattr(service, "monitor_metrics", None)
            if callable(collector):
                try:
                    raw_metrics, collector_errors = _collector_result(collector())
                    errors.extend(f"{kind}: {item}" for item in collector_errors)
                except Exception as exc:
                    errors.append(f"{kind}: monitor metrics failed: {exc}")
        card = _status_card(kind, status)
    else:
        values, resolved = instances.resolve_target(kind, target_id)
        meta = _target_meta(kind, resolved)
        collected = instances.collect_instance(kind, values, meta)
        raw_metrics = list(collected.get("metrics") or [])
        errors = [f"{kind}: {item}" for item in collected.get("errors") or []]
        card = {
            "configured": bool(values.get("enabled", True) and (
                instances.profiles._is_configured(kind, values))),
            "available": bool(collected.get("available")),
            "latency_ms": _as_number(collected.get("latency_ms")),
            "error": ("监控已停用" if collected.get("disabled")
                      else (errors[0].split(": ", 1)[-1] if errors else "")),
            "version": str(collected.get("version") or ""),
            "endpoint": str(collected.get("endpoint") or ""),
        }

    result = _finalize_collection(kind, card, raw_metrics, errors, meta, now)
    with _CACHE_LOCK:
        _CACHE[cache_key] = (now, result)
    return result


def _monitor_target_ids(kind: str) -> list[str]:
    """Primary plus every enabled extra instance, without leaking DB rows to callers."""
    try:
        targets = instances.monitor_targets(kind)
    except Exception:
        return ["primary"]
    ids = [str((target.get("meta") or {}).get("id") or "primary") for target in targets]
    return list(dict.fromkeys(ids)) or ["primary"]


def _target_failure(kind: str, target_id: str, error: str) -> dict:
    name = _NAMES.get(kind, kind)
    return {
        "kind": kind, "name": name, "instance_id": target_id,
        "instance_name": f"{name}(默认)" if target_id == "primary" else target_id,
        "is_primary": target_id == "primary", "configured": False,
        "available": False, "latency_ms": None, "version": "", "endpoint": "",
        "error": error, "metrics": [], "metrics_total": 0,
        "metrics_truncated": False, "quality": "unknown", "dimensions": [],
        "partial_errors": [f"{kind}: {error}"],
        "source_status": {kind: False}, "collected_at": int(time.time()),
        "cached": False, "cache_age_seconds": 0,
    }


def _collect_parallel(*, force: bool = False) -> list[dict]:
    targets = [(kind, target_id) for kind in _KINDS for target_id in _monitor_target_ids(kind)]
    futures = {
        (kind, target_id): _EXECUTOR.submit(
            _collect_kind, kind, force=force, instance_id=target_id,
        )
        for kind, target_id in targets
    }
    deadline = time.monotonic() + _OVERVIEW_TIMEOUT
    rows: list[dict] = []
    for kind, target_id in targets:
        wait = max(0.0, deadline - time.monotonic())
        try:
            rows.append(futures[(kind, target_id)].result(timeout=wait))
        except FutureTimeoutError:
            futures[(kind, target_id)].cancel()
            rows.append(_target_failure(kind, target_id, "monitor timeout"))
        except Exception as exc:
            rows.append(_target_failure(kind, target_id, str(exc)))
    return rows


def _sample_rows(rows: list[dict]) -> tuple[datetime, list[MiddlewareMetricSample]]:
    minute = datetime.utcnow().replace(second=0, microsecond=0)
    samples: list[MiddlewareMetricSample] = []
    for row in rows:
        # A failed or partial collection must not become a synthetic zero in history.
        if row.get("quality") == "unknown":
            continue
        for metric in row.get("metrics") or []:
            if not metric.get("numeric"):
                continue
            value = _as_number(metric.get("value"))
            if value is None:
                continue
            samples.append(MiddlewareMetricSample(
                kind=row["kind"],
                metric_key=metric["key"],
                value=value,
                unit=metric.get("unit") or "count",
                dimensions=metric.get("dimensions") or {},
                dimension_key=metric.get("dimension_key") or "",
                sampled_at=minute,
                resolution="raw",
                quality=("unknown" if row.get("quality") == "unknown"
                         else metric.get("status") or "ok"),
                source=metric.get("source") or "",
            ))
    return minute, samples


def _try_sample_db_lock(db) -> bool:
    dialect = getattr(getattr(db, "bind", None), "dialect", None)
    if getattr(dialect, "name", "") != "postgresql":
        return True
    return bool(db.execute(
        text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
        {"lock_id": _SAMPLE_DB_LOCK_ID},
    ).scalar())


def _sample_skipped(minute: datetime, reason: str) -> dict:
    return {
        "sampled_at": minute.isoformat() + "Z",
        "samples": 0,
        "skipped": True,
        "reason": reason,
        "partial_errors": [f"history: {reason}; sample skipped"],
    }

def sample_middleware_metrics(*, force: bool = True) -> dict:
    minute = datetime.utcnow().replace(second=0, microsecond=0)
    if not _SAMPLE_LOCK.acquire(blocking=False):
        return _sample_skipped(minute, "previous sampling is still running")
    try:
        rows = _collect_parallel(force=force)
        minute, samples = _sample_rows(rows)
        errors: list[str] = []
        for row in rows:
            errors.extend(row.get("partial_errors") or [])
        if samples:
            try:
                with get_db() as db:
                    if not _try_sample_db_lock(db):
                        return _sample_skipped(minute, "another replica is sampling")
                    db.execute(delete(MiddlewareMetricSample).where(MiddlewareMetricSample.sampled_at == minute))
                    db.add_all(samples)
                    db.commit()
            except Exception as exc:
                errors.append(f"history: {exc}")
        return {
            "sampled_at": minute.isoformat() + "Z",
            "samples": len(samples),
            "skipped": False,
            "partial_errors": errors,
        }
    finally:
        _SAMPLE_LOCK.release()


def _parse_range(value: str, *, end: datetime | None = None) -> tuple[datetime, datetime]:
    end_at = end or datetime.utcnow()
    aliases = {
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "6h": timedelta(hours=6),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
        "90d": timedelta(days=90),
    }
    if value not in aliases:
        raise HTTPException(status_code=422, detail="range 仅支持 15m/1h/6h/24h/7d/30d/90d")
    return end_at - aliases[value], end_at


def _range_delta(value: str) -> timedelta:
    aliases = {
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "6h": timedelta(hours=6),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
        "90d": timedelta(days=90),
    }
    if value not in aliases:
        raise HTTPException(status_code=422, detail="range 仅支持 15m/1h/6h/24h/7d/30d/90d")
    return aliases[value]


def _parse_datetime(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="时间格式不合法，请使用 ISO 8601") from exc


def _bucket_at(value: datetime, seconds: int) -> datetime:
    if seconds <= 60:
        return value.replace(second=0, microsecond=0)
    stamp = int(value.replace(tzinfo=timezone.utc).timestamp())
    return datetime.utcfromtimestamp(stamp - (stamp % seconds))


def _parse_filters(value: str) -> dict:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="filters 必须是 JSON 对象") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="filters 必须是 JSON 对象")
    return {str(key): str(item) for key, item in data.items() if item is not None and str(item) != ""}


def _metric_matches(metric: MiddlewareMetricSample, filters: dict) -> bool:
    dimensions = metric.dimensions if isinstance(metric.dimensions, dict) else {}
    for key, value in filters.items():
        if key == "instance_id" and value == "primary":
            if str(dimensions.get(key) or "") not in ("", "primary"):
                return False
            continue
        if str(dimensions.get(key) or "") != value:
            return False
    return True


def _query_timeseries(
    *,
    kind: str,
    metric_keys: list[str],
    start_at: datetime,
    end_at: datetime,
    resolution: str,
    group_by: str,
    filters: dict,
) -> dict:
    seconds = {"raw": 60, "5m": 300, "1h": 3600}.get(resolution)
    if seconds is None:
        raise HTTPException(status_code=422, detail="resolution 仅支持 raw/5m/1h")
    with get_db() as db:
        query = db.query(MiddlewareMetricSample).filter(
            MiddlewareMetricSample.kind == kind,
            MiddlewareMetricSample.resolution == "raw",
            MiddlewareMetricSample.sampled_at >= start_at,
            MiddlewareMetricSample.sampled_at <= end_at,
        )
        if metric_keys:
            query = query.filter(MiddlewareMetricSample.metric_key.in_(metric_keys))
        else:
            selected = (
                db.query(MiddlewareMetricSample.metric_key)
                .filter(
                    MiddlewareMetricSample.kind == kind,
                    MiddlewareMetricSample.sampled_at >= start_at,
                    MiddlewareMetricSample.sampled_at <= end_at,
                )
                .distinct()
                .limit(8)
                .all()
            )
            metric_keys = [str(row[0]) for row in selected]
            if metric_keys:
                query = query.filter(MiddlewareMetricSample.metric_key.in_(metric_keys))
        rows = query.order_by(MiddlewareMetricSample.sampled_at.asc()).limit(_TIMESERIES_MAX_ROWS).all()

    groups: dict[tuple[str, str, datetime], list[float]] = {}
    units: dict[str, str] = {}
    for row in rows:
        if not _metric_matches(row, filters):
            continue
        dimensions = row.dimensions if isinstance(row.dimensions, dict) else {}
        group_value = str(dimensions.get(group_by) or "") if group_by else ""
        bucket = _bucket_at(row.sampled_at, seconds)
        groups.setdefault((row.metric_key, group_value, bucket), []).append(float(row.value))
        units[row.metric_key] = row.unit or "count"

    points: dict[tuple[str, str], list[dict]] = {}
    for (metric_key, group_value, bucket), values in sorted(groups.items(), key=lambda item: item[0][2]):
        points.setdefault((metric_key, group_value), []).append({
            "timestamp": bucket.isoformat() + "Z",
            "value": round(sum(values) / len(values), 6),
            "sample_count": len(values),
        })
    series = [
        {
            "metric_key": metric_key,
            "group": group_value,
            "unit": units.get(metric_key, "count"),
            "points": values,
        }
        for (metric_key, group_value), values in points.items()
    ]
    return {
        "kind": kind,
        "start": start_at.isoformat() + "Z",
        "end": end_at.isoformat() + "Z",
        "resolution": resolution,
        "group_by": group_by,
        "filters": filters,
        "metrics": metric_keys,
        "series": series,
        "truncated": len(rows) >= _TIMESERIES_MAX_ROWS,
    }


def _cleanup_history() -> int:
    cutoff_raw = datetime.utcnow() - timedelta(days=max(1, int(MW_METRICS_RETENTION_DAYS)))
    cutoff_rollup = datetime.utcnow() - timedelta(days=max(30, int(MW_METRICS_RETENTION_DAYS) * 12))
    with get_db() as db:
        deleted = (
            db.query(MiddlewareMetricSample)
            .filter(
                MiddlewareMetricSample.resolution == "raw",
                MiddlewareMetricSample.sampled_at < cutoff_raw,
            )
            .delete(synchronize_session=False)
        )
        db.query(MiddlewareMetricSample).filter(
            MiddlewareMetricSample.resolution != "raw",
            MiddlewareMetricSample.sampled_at < cutoff_rollup,
        ).delete(synchronize_session=False)
        db.commit()
        return int(deleted or 0)


async def middleware_metrics_loop() -> None:
    last_cleanup_day = ""
    while True:
        try:
            if MW_METRICS_ENABLED:
                await asyncio.to_thread(sample_middleware_metrics, force=True)
                today = datetime.utcnow().date().isoformat()
                if today != last_cleanup_day:
                    await asyncio.to_thread(_cleanup_history)
                    last_cleanup_day = today
        except Exception as exc:
            print(f"[middleware-monitor] sampling skipped: {exc}", flush=True)
        await asyncio.sleep(max(15, int(MW_METRICS_INTERVAL_SECONDS or 60)))


@router.get("/monitor/overview")
def data_service_monitor_overview(
    force: bool = Query(False),
    _operator: object = Depends(require_configured_operator),
) -> dict:
    rows = _collect_parallel(force=force)
    metrics = [metric for row in rows for metric in row.get("metrics") or []]
    errors = [item for row in rows for item in row.get("partial_errors") or []]
    summary = {
        "services_total": len(rows),
        "services_available": sum(1 for row in rows if row.get("available")),
        "services_configured": sum(1 for row in rows if row.get("configured")),
        "metrics_total": len(metrics),
        "metrics_truncated": any(row.get("metrics_truncated") for row in rows),
        "warning_metrics": sum(1 for metric in metrics if metric.get("status") == "warning"),
        "critical_metrics": sum(1 for metric in metrics if metric.get("status") == "critical"),
        "collected_at": int(time.time()),
    }
    cards = [{
        "kind": row.get("kind"),
        "name": row.get("name"),
        "instance_id": row.get("instance_id"),
        "instance_name": row.get("instance_name"),
        "is_primary": row.get("is_primary"),
        "configured": row.get("configured"),
        "available": row.get("available"),
        "latency_ms": row.get("latency_ms"),
        "version": row.get("version"),
        "endpoint": row.get("endpoint"),
        "metric_count": len(row.get("metrics") or []),
        "metrics_total": row.get("metrics_total") or 0,
        "metrics_truncated": bool(row.get("metrics_truncated")),
        "warning_count": sum(1 for metric in row.get("metrics") or [] if metric.get("status") == "warning"),
        "critical_count": sum(1 for metric in row.get("metrics") or [] if metric.get("status") == "critical"),
        "error": row.get("error") or "",
    } for row in rows]
    return _envelope({"summary": summary, "services": cards},
                     partial_errors=errors,
                     source_status={row["kind"]: bool(row.get("available")) for row in rows})


@router.get("/{kind}/monitor")
def data_service_monitor(
    kind: str,
    force: bool = Query(False),
    instance: str = Query(""),
    _operator: object = Depends(require_configured_operator),
) -> dict:
    if kind not in _KINDS:
        raise HTTPException(status_code=404, detail=f"未知数据服务: {kind}")
    row = _collect_kind(kind, force=force, instance_id=instance)
    return _envelope(row, partial_errors=row.get("partial_errors") or [],
                     source_status=row.get("source_status") or {})


@router.get("/{kind}/metrics")
def data_service_metrics(
    kind: str,
    force: bool = Query(False),
    status: str = Query(""),
    instance: str = Query(""),
    _operator: object = Depends(require_configured_operator),
) -> dict:
    if kind not in _KINDS:
        raise HTTPException(status_code=404, detail=f"未知数据服务: {kind}")
    row = _collect_kind(kind, force=force, instance_id=instance)
    metrics = row.get("metrics") or []
    if status:
        wanted = {item.strip() for item in status.split(",") if item.strip()}
        metrics = [metric for metric in metrics if metric.get("status") in wanted]
    return _envelope(
                     {"kind": kind, "metrics": metrics,
                      "metrics_total": row.get("metrics_total") or 0,
                      "metrics_truncated": bool(row.get("metrics_truncated")),
                      "dimensions": row.get("dimensions") or []},
                     partial_errors=row.get("partial_errors") or [],
                     source_status=row.get("source_status") or {})


@router.get("/{kind}/dimensions")
def data_service_dimensions(
    kind: str,
    force: bool = Query(False),
    instance: str = Query(""),
    _operator: object = Depends(require_configured_operator),
) -> dict:
    if kind not in _KINDS:
        raise HTTPException(status_code=404, detail=f"未知数据服务: {kind}")
    row = _collect_kind(kind, force=force, instance_id=instance)
    return _envelope({"kind": kind, "dimensions": row.get("dimensions") or []},
                     partial_errors=row.get("partial_errors") or [],
                     source_status=row.get("source_status") or {})


@router.get("/{kind}/diagnostics")
def data_service_diagnostics(
    kind: str,
    force: bool = Query(False),
    instance: str = Query(""),
    _operator: object = Depends(require_configured_operator),
) -> dict:
    if kind not in _KINDS:
        raise HTTPException(status_code=404, detail=f"未知数据服务: {kind}")
    row = _collect_kind(kind, force=force, instance_id=instance)
    items: list[dict] = []
    if not row.get("configured"):
        items.append({"level": "warning", "title": "未配置", "message": f"{_NAMES[kind]} 未配置连接参数"})
    elif not row.get("available"):
        items.append({
            "level": "critical", "title": "服务不可用",
            "message": row.get("error") or "连通性探测失败",
        })
    for metric in row.get("metrics") or []:
        if metric.get("status") not in {"warning", "critical"}:
            continue
        dimensions = metric.get("dimensions") or {}
        suffix = " / ".join(f"{key}={value}" for key, value in dimensions.items())
        items.append({
            "level": metric.get("status"),
            "title": metric.get("label") or metric.get("key"),
            "message": f"{metric.get('key')} = {metric.get('value')} {metric.get('unit')}"
                       + (f"（{suffix}）" if suffix else ""),
            "metric_key": metric.get("key"),
            "dimensions": dimensions,
        })
    if not items:
        items.append({"level": "ok", "title": "状态正常", "message": "当前未发现异常指标"})
    return _envelope({"kind": kind, "items": items},
                     partial_errors=row.get("partial_errors") or [],
                     source_status=row.get("source_status") or {})


@router.get("/{kind}/timeseries")
def data_service_timeseries(
    kind: str,
    metrics: str = Query(""),
    range: str = Query("1h"),
    start: str = Query(""),
    end: str = Query(""),
    resolution: str = Query("raw"),
    group_by: str = Query(""),
    filters: str = Query(""),
    instance: str = Query(""),
    _operator: object = Depends(require_configured_operator),
) -> dict:
    if kind not in _KINDS:
        raise HTTPException(status_code=404, detail=f"未知数据服务: {kind}")
    delta = _range_delta(range)
    now = datetime.utcnow()
    end_at = _parse_datetime(end, now)
    start_at = _parse_datetime(start, end_at - delta)
    if end_at < start_at:
        raise HTTPException(status_code=422, detail="end 不能早于 start")
    if end_at - start_at > timedelta(days=90):
        raise HTTPException(status_code=422, detail="查询范围不能超过 90 天")
    metric_keys = [item.strip() for item in metrics.split(",") if item.strip()]
    if len(metric_keys) > 12:
        raise HTTPException(status_code=422, detail="单次最多查询 12 个指标")
    filter_values = _parse_filters(filters)
    if str(instance or "").strip():
        filter_values["instance_id"] = str(instance).strip()
    data = _query_timeseries(
        kind=kind,
        metric_keys=metric_keys,
        start_at=start_at,
        end_at=end_at,
        resolution=resolution,
        group_by=group_by,
        filters=filter_values,
    )
    available = bool(data["series"])
    return _envelope(data, partial_errors=[] if available else ["history: 暂无采样数据"],
                     source_status={"history": available})
