"""Module-owned Agent sampling and metric history API."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import Depends, HTTPException

from agent import AgentSampler
from collector import _query_range
from runtime.app import create_module_app, require_internal_service
from storage import MonitorStore


store = MonitorStore()
sampler = AgentSampler(store)


def _age(value):
    if value is None:
        return None
    return max(0, (datetime.now(timezone.utc) - value.replace(tzinfo=timezone.utc)).total_seconds())


def summary_provider():
    hosts = store.hosts()
    fresh = [item for item in hosts if item["last_sample_at"] and _age(item["last_sample_at"]) <= 120]
    latest = max((item["last_sample_at"] for item in hosts if item["last_sample_at"]), default=None)
    return {
        "status": "ok" if fresh and len(fresh) == len(hosts) else "degraded" if latest else "unavailable",
        "collected_at": latest.replace(tzinfo=timezone.utc) if latest else None,
        "stale_after_seconds": 120,
        "data": {"hosts_with_metrics": len(fresh), "hosts_total": len(hosts)},
        "error": None if fresh and len(fresh) == len(hosts) else "部分主机指标暂不可用或已过期",
    }


def findings_provider():
    hosts = store.hosts()
    items = [{
        "id": f"host-monitor:{item['id']}:stale", "resource_id": str(item["id"]),
        "severity": "warning", "title": f"{item['name']} 监控数据过期",
        "summary": "最近 120 秒没有新的 Agent 指标", "observed_at": item["last_sample_at"] or datetime.now(timezone.utc),
    } for item in hosts if not item["last_sample_at"] or _age(item["last_sample_at"]) > 120]
    return {"status": "degraded" if items else "ok", "collected_at": datetime.now(timezone.utc),
            "items": items, "error": None}


app = create_module_app(
    key="host-monitor", version="0.2.3", summary_provider=summary_provider,
    findings_provider=findings_provider,
    capabilities=["summary:read", "findings:read", "metrics:read"],
    readiness_provider=store.ping,
)


@app.on_event("startup")
def start_sampler():
    store.migrate()
    sampler.start()


@app.on_event("shutdown")
def stop_sampler():
    sampler.stop()


@app.get("/api/v1/hosts/{host_id}/system/summary")
def system_summary(host_id: UUID, refresh: bool = False):
    if refresh and str(host_id) == sampler.host_id:
        try:
            sampler.refresh()
        except Exception:
            pass
    data = store.latest(str(host_id))
    if data is None:
        raise HTTPException(503, "主机监控数据尚未采集")
    age = max(0, datetime.now(timezone.utc).timestamp() - float(data["timestamp"]))
    return {**data, "cached": True, "stale": age > 120,
            "cache_age_seconds": round(age, 2), "cache_ttl_seconds": 120}


@app.get("/api/v1/internal/metrics/latest", dependencies=[Depends(require_internal_service)])
def internal_latest_metrics():
    hosts = []
    for row in store.hosts():
        summary = store.latest(str(row["id"])) or {}
        hosts.append({"id": str(row["id"]), "name": row["name"], "last_sample_at": row["last_sample_at"],
                      "metrics": summary.get("metrics", {})})
    return {"collected_at": datetime.now(timezone.utc), "items": hosts}


def _query_window(start: str, end: str, resolution: str, metrics: str):
    query = _query_range(start, end, resolution, metrics)
    start_at = datetime.fromisoformat(query["start"].replace("Z", "+00:00")).astimezone(timezone.utc)
    end_at = datetime.fromisoformat(query["end"].replace("Z", "+00:00")).astimezone(timezone.utc)
    if end_at - start_at > timedelta(days=366 * 5):
        raise HTTPException(400, "时间范围不得超过五年")
    selected = resolution
    if selected == "auto":
        selected = "raw" if end_at-start_at <= timedelta(hours=24) else (
            "5m" if end_at-start_at <= timedelta(days=90) else "1h")
    return query["metrics"].split(","), start_at.replace(tzinfo=None), end_at.replace(tzinfo=None), selected


@app.get("/api/v1/hosts/{host_id}/metrics/timeseries")
def host_timeseries(host_id: UUID, start: str, end: str, resolution: str = "auto",
                    metrics: str = "cpu,memory,disk"):
    names, start_at, end_at, selected = _query_window(start, end, resolution, metrics)
    if not store.host(str(host_id)):
        raise HTTPException(404, "主机不存在")
    series = store.series(str(host_id), names, start_at, end_at, selected)
    return {"server_id": str(host_id), "metrics": names, "resolution": selected,
            "start": start_at.isoformat() + "Z", "end": end_at.isoformat() + "Z",
            "series": series, "point_count": sum(len(items) for items in series.values())}


@app.get("/api/v1/metrics/hosts/overview")
def hosts_overview(start: str, end: str, resolution: str = "auto",
                   metrics: str = "cpu,memory,disk"):
    names, start_at, end_at, selected = _query_window(start, end, resolution, metrics)
    return {"metrics": names, "resolution": selected,
            "start": start_at.isoformat() + "Z", "end": end_at.isoformat() + "Z",
            "hosts": store.overview(names, start_at, end_at, selected)}
