from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
import re
import shutil
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from fastapi import Header, HTTPException, Query

from runtime.app import create_module_app
from runtime.database import database_ready
from pathlib import Path


LOKI_URL = os.getenv("LOGS_LOKI_URL", "").rstrip("/")
HOST_INVENTORY_URL = os.getenv("HOST_INVENTORY_URL", "http://module-host-inventory:8080").rstrip("/")
TIMEOUT = float(os.getenv("LOGS_TIMEOUT_SECONDS", "8"))
RETENTION_DAYS = max(1, int(os.getenv("LOGS_RETENTION_DAYS", "365")))
LOKI_DATA_DIR = os.getenv("LOGS_DATA_DIR", "")
SOURCES = {"all", "journal", "docker"}
SAFE_LABEL = re.compile(r"^[\w.@:/ -]{1,160}$")
PROM_LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+([-+0-9.eE]+)$")
PROM_LABEL = re.compile(r'(\w+)="((?:\\.|[^"\\])*)"')


def _request(url: str, *, params: dict | None = None, headers: dict | None = None, text: bool = False):
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params)
    request = Request(url, headers={"Accept": "text/plain" if text else "application/json", **(headers or {})})
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            body = response.read().decode("utf-8", "replace")
        return body if text else json.loads(body)
    except HTTPError as error:
        if error.code in (401, 403):
            raise HTTPException(error.code, "未授权访问日志数据") from error
        raise HTTPException(502, "日志数据源暂不可用") from error
    except (TimeoutError, URLError, ValueError, OSError) as error:
        raise HTTPException(502, "日志数据源暂不可用") from error


def _loki(path: str, params: dict | None = None):
    if not LOKI_URL:
        raise HTTPException(503, "日志中心尚未配置 LOKI_URL")
    return _request(f"{LOKI_URL}{path}", params=params)


def _parse_time(value: str | None, default: datetime) -> datetime:
    if not value:
        return default
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(400, "时间格式无效，请使用 ISO 8601") from error
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def _window(start: str | None, end: str | None, label: str):
    end_dt = _parse_time(end, datetime.now(timezone.utc).replace(tzinfo=None))
    start_dt = _parse_time(start, end_dt - timedelta(hours=1))
    if start_dt >= end_dt or end_dt - start_dt > timedelta(days=RETENTION_DAYS):
        raise HTTPException(400, f"{label}时间范围必须在 {RETENTION_DAYS} 天以内")
    return start_dt, end_dt


def _selector(host_id: UUID, source: str, service: str | None, search: str | None) -> str:
    if source not in SOURCES:
        raise HTTPException(400, "日志来源无效")
    labels = [f'server_id="{host_id}"']
    if source != "all":
        labels.append(f'source="{source}"')
    if service:
        if not SAFE_LABEL.fullmatch(service):
            raise HTTPException(400, "服务名包含非法字符")
        labels.append(f'service_name="{service.replace(chr(92), chr(92)*2).replace(chr(34), chr(92)+chr(34))}"')
    selector = "{" + ",".join(labels) + "}"
    if search:
        if len(search) > 256 or "\x00" in search:
            raise HTTPException(400, "搜索关键字最长 256 字符")
        escaped = search.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        selector += f' |= "{escaped}"'
    return selector


def _range(selector: str, seconds: int) -> str:
    return f"{selector}{' ' if '|' in selector else ''}[{seconds}s]"


def _vector(payload: dict, label: str | None = None) -> dict[str, float]:
    result = {}
    for item in payload.get("data", {}).get("result", []):
        key = item.get("metric", {}).get(label, "") if label else "total"
        try:
            result[key or "unknown"] = float(item.get("value", [0, "0"])[1])
        except (TypeError, ValueError, IndexError):
            continue
    return result


def _matrix(payload: dict) -> list[list[float]]:
    points = {}
    for item in payload.get("data", {}).get("result", []):
        for stamp, value in item.get("values", []):
            try:
                second = int(float(stamp))
                points[second] = points.get(second, 0.0) + float(value)
            except (TypeError, ValueError):
                continue
    return [[stamp, round(value, 4)] for stamp, value in sorted(points.items())]


def _auto_step(start: datetime, end: datetime) -> int:
    span = end - start
    return 60 if span <= timedelta(hours=6) else 300 if span <= timedelta(days=2) else 3600 if span <= timedelta(days=30) else 21600


def _hosts(authorization: str | None = None) -> list[dict]:
    headers = {"Authorization": authorization} if authorization else {}
    result = _request(f"{HOST_INVENTORY_URL}/api/v1/hosts", headers=headers)
    return result.get("items", []) if isinstance(result, dict) else []


def _host(host_id: UUID, authorization: str | None):
    host = next((item for item in _hosts(authorization) if str(item.get("id")) == str(host_id)), None)
    if not host:
        raise HTTPException(404, "主机不存在")
    return host


def _coverage():
    token_file = os.getenv("HOST_INVENTORY_INTERNAL_TOKEN_FILE", "")
    if not token_file:
        return None
    token = Path(token_file).read_text(encoding="utf-8").strip()
    result = _request(f"{HOST_INVENTORY_URL}/api/v1/internal/log-coverage",
                      headers={"X-OpsCenter-Module-Token": token})
    if result.get("status") != "ok" or not isinstance(result.get("items"), list):
        raise ValueError("Invalid log coverage")
    fresh = _vector(_loki("/loki/api/v1/query", {"query": 'sum by (server_id) (count_over_time({server_id=~".+"}[10m]))'}), "server_id")
    return [{**host, "ingestion_fresh": fresh.get(str(host["id"]), 0) > 0} for host in result["items"]]


def _summary():
    ready = False
    if LOKI_URL:
        try:
            with urlopen(Request(LOKI_URL + "/ready"), timeout=min(3, TIMEOUT)) as response:
                ready = response.status < 400
        except (HTTPError, TimeoutError, URLError, OSError):
            pass
    data = {"loki_configured": bool(LOKI_URL), "loki_ready": ready}
    error = None
    try:
        hosts = _coverage()
        if hosts is None:
            error = "日志采集覆盖检查尚未配置"
        else:
            running = sum(host.get("log_agent_status") == "running" for host in hosts)
            fresh = sum(bool(host.get("ingestion_fresh")) for host in hosts)
            data.update(hosts_total=len(hosts), collectors_running=running, collectors_missing=len(hosts)-running,
                        collectors_fresh=fresh, collectors_stale=len(hosts)-fresh)
    except (OSError, HTTPException, ValueError):
        error = "日志采集覆盖状态暂不可用"
    return {"status": "ok" if ready and not error and not data.get("collectors_missing") and not data.get("collectors_stale") else "degraded",
            "collected_at": datetime.now(timezone.utc), "stale_after_seconds": 120, "data": data, "error": error}


def _findings():
    hosts = _coverage()
    if hosts is None:
        raise ValueError("Log coverage is not configured")
    items = [{"id": f"logs:{host['id']}:collector", "resource_id": str(host["id"]), "severity": "warning",
              "title": f"{host['name']} 日志采集异常",
              "summary": "当前主机没有运行中的日志采集器" if host.get("log_agent_status") != "running" else "采集器运行中，但最近10分钟没有日志送达",
              "observed_at": datetime.now(timezone.utc)} for host in (hosts or [])
             if host.get("log_agent_status") != "running" or not host.get("ingestion_fresh")]
    return {"status": "degraded" if items else "ok", "collected_at": datetime.now(timezone.utc), "items": items}


app = create_module_app(key="logs", version="0.2.2", summary_provider=_summary,
                        findings_provider=_findings,
                        capabilities=["summary:read", "findings:read", "logs:read", "health:read"],
                        readiness_provider=database_ready)


@app.get("/api/v1/status")
def status():
    if not LOKI_URL:
        return {"configured": False, "ready": False, "retention_days": RETENTION_DAYS, "message": "日志存储暂不可用"}
    try:
        with urlopen(Request(LOKI_URL + "/ready"), timeout=min(3, TIMEOUT)) as response:
            ready = response.status < 400
    except (HTTPError, TimeoutError, URLError, OSError):
        ready = False
    return {"configured": True, "ready": ready, "retention_days": RETENTION_DAYS,
            "message": "日志存储正常" if ready else "日志存储暂不可用"}


@app.get("/api/v1/health")
def health():
    disk = {"available": False, "path": LOKI_DATA_DIR}
    if LOKI_DATA_DIR and os.path.isdir(LOKI_DATA_DIR):
        usage = shutil.disk_usage(LOKI_DATA_DIR)
        disk = {"available": True, "path": LOKI_DATA_DIR, "total_bytes": usage.total, "used_bytes": usage.used,
                "free_bytes": usage.free, "used_percent": round(usage.used * 100 / usage.total, 1) if usage.total else 0}
    result = {"configured": bool(LOKI_URL), "data_dir": disk, "runtime": {}, "index_24h": {}, "warnings": []}
    if not LOKI_URL:
        result["warnings"].append("Loki 尚未配置")
        return result
    now = datetime.now(timezone.utc)
    start = int((now - timedelta(hours=24)).timestamp() * 1_000_000_000)
    end = int(now.timestamp() * 1_000_000_000)
    try:
        stats = _loki("/loki/api/v1/index/stats", {"query": '{server_id=~".+"}', "start": start, "end": end})
        result["index_24h"] = stats.get("data") or stats
    except HTTPException:
        result["warnings"].append("近 24 小时索引暂不可用")
    try:
        samples = []
        for line in _request(LOKI_URL + "/metrics", text=True).splitlines():
            match = PROM_LINE.match(line.strip())
            if match:
                labels = dict(PROM_LABEL.findall(match.group(2) or ""))
                try:
                    samples.append((match.group(1), labels, float(match.group(3))))
                except ValueError:
                    pass
        total = lambda name: sum(value for metric, _, value in samples if metric == name)
        result["runtime"] = {
            "received_lines_total": int(total("loki_distributor_lines_received_total")),
            "received_bytes_total": int(total("loki_distributor_bytes_received_total")),
            "discarded_samples_total": int(total("loki_discarded_samples_total")),
            "write_5xx_total": int(sum(value for metric, labels, value in samples if metric == "loki_request_duration_seconds_count" and labels.get("status_code", "").startswith("5") and "push" in labels.get("route", "").lower())),
            "append_timeouts_total": int(total("loki_distributor_ingester_append_timeouts_total")),
            "panic_total": int(total("loki_panic_total")),
            "flush_queue_length": int(total("loki_ingester_flush_queue_length")),
        }
    except HTTPException:
        result["warnings"].append("Loki 运行指标暂不可用")
    if disk.get("available") and disk.get("used_percent", 0) >= 80:
        result["warnings"].append(f"Loki 数据盘使用率已达 {disk['used_percent']}%")
    runtime = result["runtime"]
    failures = sum(runtime.get(key, 0) for key in ("discarded_samples_total", "write_5xx_total", "append_timeouts_total", "panic_total"))
    if failures:
        result["warnings"].append(f"Loki 累计写入异常指标 {failures} 次")
    if runtime.get("flush_queue_length", 0) > 0:
        result["warnings"].append(f"Loki flush 队列积压 {runtime['flush_queue_length']}")
    return result


@app.get("/api/v1/agents/overview")
def agents_overview(probe: bool = False, authorization: str | None = Header(default=None)):
    agents = []
    for host in _hosts(authorization):
        item = {"server_id": host.get("id"), "name": host.get("name"), "host": host.get("host"),
                "is_local": host.get("is_local", False), "collector_status": host.get("log_agent_status") or "unknown",
                "collector_version": host.get("log_agent_version") or "", "checked_at": host.get("log_agent_checked_at"),
                "ingestion_status": "not_probed", "last_log_at": None, "age_seconds": None}
        if probe and LOKI_URL:
            try:
                end = datetime.now(timezone.utc)
                payload = _loki("/loki/api/v1/query_range", {"query": f'{{server_id="{item["server_id"]}"}}',
                    "start": int((end - timedelta(minutes=10)).timestamp() * 1_000_000_000),
                    "end": int(end.timestamp() * 1_000_000_000), "limit": 1, "direction": "backward"})
                values = [entry[0] for stream in payload.get("data", {}).get("result", []) for entry in stream.get("values", [])]
                stamp = max((int(value) for value in values), default=0)
                item["age_seconds"] = max(0, int(end.timestamp() - stamp / 1_000_000_000)) if stamp else None
                item["last_log_at"] = datetime.fromtimestamp(stamp / 1_000_000_000, timezone.utc).isoformat() if stamp else None
                item["ingestion_status"] = "fresh" if stamp and item["age_seconds"] <= 600 else "no_data" if not stamp else "stale"
            except HTTPException:
                item["ingestion_status"] = "error"
        if item["collector_status"] == "error" or item["ingestion_status"] == "error":
            item.update(diagnostic_level="error", diagnostic="采集状态异常" if item["collector_status"] == "error" else "Loki 探测失败")
        elif item["collector_status"] == "running" and item["ingestion_status"] in {"stale", "no_data"}:
            item.update(diagnostic_level="warning", diagnostic="最近日志已超过10分钟" if item["ingestion_status"] == "stale" else "最近24小时没有新日志")
        elif item["collector_status"] in {"running", "deploying", "checking"}:
            item.update(diagnostic_level="ok" if item["collector_status"] == "running" else "info", diagnostic="采集正常" if item["collector_status"] == "running" else "正在处理")
        else:
            item.update(diagnostic_level="warning", diagnostic="日志采集器未部署")
        agents.append(item)
    total = len(agents)
    return {"total": total, "running": sum(item["collector_status"] == "running" for item in agents),
            "fresh": sum(item["ingestion_status"] == "fresh" for item in agents),
            "abnormal": sum(item["diagnostic_level"] in {"warning", "error"} for item in agents),
            "coverage_percent": round(sum(item["collector_status"] == "running" for item in agents) * 100 / total, 1) if total else 0,
            "probed": probe, "loki_configured": bool(LOKI_URL), "agents": agents}


@app.get("/api/v1/hosts/{host_id}/query")
def query_logs(host_id: UUID, start: str | None = None, end: str | None = None,
               source: str = "all", service: str | None = Query(default=None, max_length=160),
               search: str | None = Query(default=None, max_length=256), limit: int = Query(default=500, ge=1, le=5000),
               direction: str = Query(default="backward", pattern="^(backward|forward)$"),
               authorization: str | None = Header(default=None)):
    host = _host(host_id, authorization)
    start_dt, end_dt = _window(start, end, "查询")
    selector = _selector(host_id, source, service, search)
    payload = _loki("/loki/api/v1/query_range", {"query": selector, "start": int(start_dt.replace(tzinfo=timezone.utc).timestamp() * 1e9),
        "end": int(end_dt.replace(tzinfo=timezone.utc).timestamp() * 1e9), "limit": limit, "direction": direction})
    allowed = {"source", "service_name", "unit", "container", "image", "level", "priority"}
    entries = [{"timestamp_ns": stamp, "line": line, "labels": {key: val for key, val in stream.get("stream", {}).items() if key in allowed}}
               for stream in payload.get("data", {}).get("result", []) for stamp, line in stream.get("values", [])]
    entries.sort(key=lambda row: int(row["timestamp_ns"]), reverse=direction == "backward")
    return {"server_id": str(host_id), "server_name": host.get("name"), "query": selector,
            "start": start_dt.isoformat() + "Z", "end": end_dt.isoformat() + "Z", "entries": entries[:limit],
            "count": min(len(entries), limit), "retention_days": RETENTION_DAYS}


def _stats(host_id: UUID, start: str | None, end: str | None, source: str, service: str | None, search: str | None):
    start_dt, end_dt = _window(start, end, "统计")
    selector = _selector(host_id, source, service, search)
    span = max(1, int((end_dt - start_dt).total_seconds()))
    escaped = selector + ' |~ "(?i)(error|fatal|panic|exception|critical|emerg)"'
    expressions = {"total": f"sum(count_over_time({_range(selector, span)}))",
                   "sources": f"sum by (source) (count_over_time({_range(selector, span)}))",
                   "levels": f"sum by (level) (count_over_time({_range(selector, span)}))",
                   "services": f"topk(10, sum by (service_name) (count_over_time({_range(selector, span)})))",
                   "errors": f"sum(count_over_time({_range(escaped, span)}))"}
    def query(expr):
        return _loki("/loki/api/v1/query", {"query": expr, "time": int(end_dt.replace(tzinfo=timezone.utc).timestamp() * 1e9)})
    with ThreadPoolExecutor(max_workers=5) as pool:
        values = dict(zip(expressions, pool.map(query, expressions.values())))
    sources, levels, services = (_vector(values[key], label) for key, label in (("sources", "source"), ("levels", "level"), ("services", "service_name")))
    return {"server_id": str(host_id), "start": start_dt.isoformat()+"Z", "end": end_dt.isoformat()+"Z",
            "total": int(_vector(values["total"]).get("total", 0)), "error_count": int(_vector(values["errors"]).get("total", 0)),
            "sources": {key: int(value) for key, value in sources.items()}, "levels": {key: int(value) for key, value in levels.items()},
            "top_services": [{"name": key, "count": int(value)} for key, value in sorted(services.items(), key=lambda pair: pair[1], reverse=True)]}


@app.get("/api/v1/hosts/{host_id}/stats")
def host_stats(host_id: UUID, start: str | None = None, end: str | None = None, source: str = "all",
               service: str | None = Query(default=None, max_length=160), search: str | None = Query(default=None, max_length=256),
               authorization: str | None = Header(default=None)):
    _host(host_id, authorization)
    return _stats(host_id, start, end, source, service, search)


@app.get("/api/v1/hosts/{host_id}/timeseries")
def host_timeseries(host_id: UUID, start: str | None = None, end: str | None = None, source: str = "all",
                    service: str | None = Query(default=None, max_length=160), search: str | None = Query(default=None, max_length=256),
                    authorization: str | None = Header(default=None)):
    _host(host_id, authorization)
    start_dt, end_dt = _window(start, end, "趋势")
    selector = _selector(host_id, source, service, search)
    error_selector = selector + ' |~ "(?i)(error|fatal|panic|exception|critical|emerg)"'
    step = _auto_step(start_dt, end_dt)
    expressions = {"total": f"sum(count_over_time({_range(selector, step)}))",
                   "errors": f"sum(count_over_time({_range(error_selector, step)}))"}
    def query(expr):
        return _loki("/loki/api/v1/query_range", {"query": expr,
            "start": int(start_dt.replace(tzinfo=timezone.utc).timestamp() * 1e9),
            "end": int(end_dt.replace(tzinfo=timezone.utc).timestamp() * 1e9), "step": step})
    with ThreadPoolExecutor(max_workers=2) as pool:
        payloads = dict(zip(expressions, pool.map(query, expressions.values())))
    return {"server_id": str(host_id), "start": start_dt.isoformat()+"Z", "end": end_dt.isoformat()+"Z",
            "step_seconds": step, "series": {key: _matrix(payloads[key]) for key in expressions}}
