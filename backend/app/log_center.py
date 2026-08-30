"""Safe, host-scoped Loki query gateway for the OpsCenter log console."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import re
import uuid

from fastapi import APIRouter, HTTPException, Query
import requests

from app.config import LOKI_RETENTION_DAYS, LOKI_TIMEOUT_SECONDS, LOKI_URL
from app.database import get_db
from app.models import Server


router = APIRouter(prefix="/api/v2", tags=["log-center"])
_SOURCE_VALUES = {"all", "journal", "docker"}
_SAFE_LABEL = re.compile(r"^[\w.@:/ -]{1,160}$")


def _parse_time(value: str | None, default: datetime) -> datetime:
    if not value:
        return default
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "时间格式无效，请使用 ISO 8601")
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _escape_logql(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _selector(server_id: uuid.UUID, source: str, service: str | None, search: str | None) -> str:
    labels = [f'server_id="{server_id}"']
    if source != "all":
        labels.append(f'source="{source}"')
    if service:
        if not _SAFE_LABEL.fullmatch(service):
            raise HTTPException(400, "服务名包含非法字符")
        labels.append(f'service_name="{_escape_logql(service)}"')
    query = "{" + ",".join(labels) + "}"
    if search:
        if len(search) > 256 or "\x00" in search:
            raise HTTPException(400, "搜索关键字最长 256 字符")
        query += f' |= "{_escape_logql(search)}"'
    return query


def _loki_get(path: str, params: dict | None = None) -> dict:
    if not LOKI_URL:
        raise HTTPException(503, "日志中心尚未配置 LOKI_URL")
    try:
        response = requests.get(f"{LOKI_URL}{path}", params=params, timeout=LOKI_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(502, f"Loki 访问失败：{str(exc)[:240]}")
    if payload.get("status") == "error":
        raise HTTPException(502, f"Loki 查询失败：{payload.get('error', '未知错误')}")
    return payload


def _vector(payload: dict, label: str | None = None) -> dict[str, float]:
    values = {}
    for item in payload.get("data", {}).get("result", []):
        key = item.get("metric", {}).get(label, "") if label else "total"
        try:
            values[key or "unknown"] = float(item.get("value", [0, "0"])[1])
        except (TypeError, ValueError, IndexError):
            continue
    return values


@router.get("/logs/status")
def log_center_status():
    if not LOKI_URL:
        return {"configured": False, "ready": False, "retention_days": LOKI_RETENTION_DAYS}
    try:
        response = requests.get(f"{LOKI_URL}/ready", timeout=min(3.0, LOKI_TIMEOUT_SECONDS))
        ready = response.ok
        message = response.text.strip()[:160]
    except requests.RequestException as exc:
        ready, message = False, str(exc)[:160]
    return {"configured": True, "ready": ready, "message": message, "retention_days": LOKI_RETENTION_DAYS}


@router.get("/servers/{server_id}/logs/query")
def query_host_logs(
    server_id: str,
    start: str | None = None,
    end: str | None = None,
    source: str = Query("all"),
    service: str | None = None,
    search: str | None = None,
    limit: int = Query(500, ge=1, le=5000),
    direction: str = Query("backward", pattern="^(backward|forward)$"),
):
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(404, "主机不存在")
    if source not in _SOURCE_VALUES:
        raise HTTPException(400, "日志来源无效")
    end_dt = _parse_time(end, datetime.utcnow())
    start_dt = _parse_time(start, end_dt - timedelta(hours=1))
    max_span = timedelta(days=max(1, LOKI_RETENTION_DAYS))
    if start_dt >= end_dt or end_dt - start_dt > max_span:
        raise HTTPException(400, f"时间范围必须在 {LOKI_RETENTION_DAYS} 天以内")
    with get_db() as db:
        server = db.query(Server).filter(Server.id == server_uuid).first()
        if not server:
            raise HTTPException(404, "主机不存在")
        server_name = server.name
    query = _selector(server_uuid, source, service, search)
    payload = _loki_get("/loki/api/v1/query_range", {
        "query": query,
        "start": str(int(start_dt.replace(tzinfo=timezone.utc).timestamp() * 1_000_000_000)),
        "end": str(int(end_dt.replace(tzinfo=timezone.utc).timestamp() * 1_000_000_000)),
        "limit": limit,
        "direction": direction,
    })
    entries = []
    allowed_labels = {"source", "service_name", "unit", "container", "image", "level", "priority"}
    for stream in payload.get("data", {}).get("result", []):
        labels = {key: value for key, value in stream.get("stream", {}).items() if key in allowed_labels}
        for timestamp_ns, line in stream.get("values", []):
            entries.append({"timestamp_ns": timestamp_ns, "line": line, "labels": labels})
    entries.sort(key=lambda item: int(item["timestamp_ns"]), reverse=direction == "backward")
    return {
        "server_id": server_id, "server_name": server_name, "query": query,
        "start": start_dt.isoformat() + "Z", "end": end_dt.isoformat() + "Z",
        "entries": entries[:limit], "count": min(len(entries), limit),
        "retention_days": LOKI_RETENTION_DAYS,
    }


@router.get("/servers/{server_id}/logs/stats")
def host_log_stats(
    server_id: str,
    start: str | None = None,
    end: str | None = None,
    source: str = Query("all"),
    service: str | None = None,
    search: str | None = None,
):
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(404, "主机不存在")
    if source not in _SOURCE_VALUES:
        raise HTTPException(400, "日志来源无效")
    end_dt = _parse_time(end, datetime.utcnow())
    start_dt = _parse_time(start, end_dt - timedelta(hours=1))
    if start_dt >= end_dt or end_dt - start_dt > timedelta(days=30):
        raise HTTPException(400, "日志统计时间范围必须在 30 天以内")
    with get_db() as db:
        if not db.query(Server.id).filter(Server.id == server_uuid).first():
            raise HTTPException(404, "主机不存在")
    selector = _selector(server_uuid, source, service, search)
    window = f"{max(1, int((end_dt - start_dt).total_seconds()))}s"
    ranged_selector = f"{selector} [{window}]" if "|=" in selector else f"{selector}[{window}]"
    expression = f"count_over_time({ranged_selector})"

    def query(logql: str) -> dict:
        return _loki_get("/loki/api/v1/query", {
            "query": logql,
            "time": str(int(end_dt.replace(tzinfo=timezone.utc).timestamp() * 1_000_000_000)),
        })

    expressions = {
        "total": f"sum({expression})",
        "sources": f"sum by (source) ({expression})",
        "levels": f"sum by (level) ({expression})",
        "services": f"topk(10, sum by (service_name) ({expression}))",
    }
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="loki-stats") as pool:
        futures = {key: pool.submit(query, logql) for key, logql in expressions.items()}
        payloads = {key: future.result() for key, future in futures.items()}
    total = _vector(payloads["total"]).get("total", 0)
    sources = _vector(payloads["sources"], "source")
    levels = _vector(payloads["levels"], "level")
    services = _vector(payloads["services"], "service_name")
    error_count = sum(value for key, value in levels.items() if key.lower() in {"error", "err", "critical", "crit", "fatal", "emerg"})
    return {
        "server_id": server_id, "start": start_dt.isoformat() + "Z", "end": end_dt.isoformat() + "Z",
        "total": int(total), "error_count": int(error_count),
        "sources": {key: int(value) for key, value in sources.items()},
        "levels": {key: int(value) for key, value in levels.items()},
        "top_services": [
            {"name": key, "count": int(value)}
            for key, value in sorted(services.items(), key=lambda item: item[1], reverse=True)
        ],
    }
