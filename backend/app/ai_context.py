"""Strictly authenticated, read-only system context APIs for AI clients."""
from __future__ import annotations

from datetime import datetime, timedelta
import re
from urllib.parse import urlsplit, urlunsplit
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func

from app.api_keys import require_api_key
from app.database import get_db
from app.models import (
    AlertEvent,
    AlertRule,
    MetricHistory,
    PlazaHealthIncident,
    PlazaHealthSilence,
    PlazaHealthState,
    Server,
    Service,
)
from app.plaza import _load_plaza_items
from app.version import VERSION


router = APIRouter(
    prefix="/api/v2/ai",
    tags=["ai-context"],
    dependencies=[Depends(require_api_key("read", required=True))],
)

SCHEMA_VERSION = "1.0"
_METRICS = ("cpu", "memory", "disk", "load1", "net_rx", "net_tx")
_ACTIVE_ALERT_STATES = ("pending", "firing")
_ACTIVE_INCIDENT_STATES = ("open", "acknowledged")
_SENSITIVE_TEXT = re.compile(
    r"(?i)(password|passwd|token|secret|api[_-]?key|authorization)\s*([=:])\s*([^\s,;]+)"
)


def _envelope(data, *, warnings: list[str] | None = None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "opscenter_version": VERSION,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "warnings": warnings or [],
        "data": data,
    }


def _age_seconds(value: datetime | None, now: datetime) -> int | None:
    return max(0, int((now - value).total_seconds())) if value else None


def _safe_url(value: str | None) -> str:
    """Expose a usable endpoint without URL credentials, query secrets or fragments."""
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return ""
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        netloc = f"{host}:{parsed.port}" if parsed.port else host
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    except (TypeError, ValueError):
        return ""


def _safe_error(value: str | None) -> str:
    if not value:
        return ""
    return _SENSITIVE_TEXT.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)[:300]


def _latest_metrics(db, server_ids: list[uuid.UUID]) -> dict[tuple[uuid.UUID, str], MetricHistory]:
    if not server_ids:
        return {}
    latest_times = db.query(
        MetricHistory.server_id.label("server_id"),
        MetricHistory.metric.label("metric"),
        func.max(MetricHistory.timestamp).label("last_at"),
    ).filter(
        MetricHistory.server_id.in_(server_ids),
        MetricHistory.metric.in_(_METRICS),
    ).group_by(MetricHistory.server_id, MetricHistory.metric).subquery()
    rows = db.query(MetricHistory).join(latest_times, and_(
        MetricHistory.server_id == latest_times.c.server_id,
        MetricHistory.metric == latest_times.c.metric,
        MetricHistory.timestamp == latest_times.c.last_at,
    )).all()
    return {(row.server_id, row.metric): row for row in rows}


def _host_rows() -> list[dict]:
    now = datetime.utcnow()
    with get_db() as db:
        servers = db.query(Server).order_by(Server.name.asc(), Server.host.asc()).all()
        ids = [row.id for row in servers]
        metrics = _latest_metrics(db, ids)
        service_counts = dict(db.query(Service.server_id, func.count(Service.id)).group_by(Service.server_id).all())
        alert_counts = dict(db.query(AlertEvent.server_id, func.count(AlertEvent.id)).filter(
            AlertEvent.status.in_(_ACTIVE_ALERT_STATES),
        ).group_by(AlertEvent.server_id).all())
        result = []
        for server in servers:
            metric_values = {}
            metric_times = []
            for name in _METRICS:
                row = metrics.get((server.id, name))
                if row:
                    age = _age_seconds(row.timestamp, now)
                    metric_times.append(row.timestamp)
                    metric_values[name] = {
                        "value": round(float(row.value), 3),
                        "collected_at": row.timestamp.isoformat() + "Z",
                        "age_seconds": age,
                        "stale": age is None or age > 90,
                    }
                else:
                    metric_values[name] = None
            latest_metric_at = max(metric_times) if metric_times else None
            result.append({
                "id": str(server.id),
                "name": server.name,
                "address": server.host,
                "status": server.status or "unknown",
                "enabled": server.enabled is not False,
                "agent": {
                    "type": server.agent_type or "remote",
                    "status": server.agent_status or "unknown",
                    "version": server.agent_version or "",
                    "last_seen": server.last_seen.isoformat() + "Z" if server.last_seen else None,
                    "last_seen_age_seconds": _age_seconds(server.last_seen, now),
                },
                "log_agent": {
                    "status": server.log_agent_status or "unknown",
                    "version": server.log_agent_version or "",
                    "checked_at": server.log_agent_checked_at.isoformat() + "Z" if server.log_agent_checked_at else None,
                },
                "service_count": int(service_counts.get(server.id, 0)),
                "active_alert_count": int(alert_counts.get(server.id, 0)),
                "latest_metric_at": latest_metric_at.isoformat() + "Z" if latest_metric_at else None,
                "metrics": metric_values,
            })
        return result


def _service_rows() -> list[dict]:
    now = datetime.utcnow()
    catalog, servers = _load_plaza_items()
    keys = [item["key"] for item in catalog if item.get("enabled")]
    with get_db() as db:
        states = {row.plaza_key: row for row in db.query(PlazaHealthState).filter(
            PlazaHealthState.plaza_key.in_(keys),
        ).all()} if keys else {}
        silenced = {row.plaza_key: row.ends_at for row in db.query(PlazaHealthSilence).filter(
            PlazaHealthSilence.plaza_key.in_(keys),
            PlazaHealthSilence.starts_at <= now,
            PlazaHealthSilence.ends_at > now,
            PlazaHealthSilence.ended_at == None,  # noqa: E711
        ).all()} if keys else {}
    result = []
    for item in catalog:
        if not item.get("enabled"):
            continue
        state = states.get(item["key"])
        server = servers.get(item.get("server_host"))
        status = "disabled" if item.get("probe_enabled") is False else (
            state.stable_status if state else "unknown"
        )
        result.append({
            "key": item["key"],
            "name": item.get("name") or item["key"],
            "category": item.get("category") or "未分类",
            "entry_url": _safe_url(item.get("entry_url")),
            "health_url": _safe_url(item.get("health_url")),
            "server": {
                "id": str(server.id) if server else None,
                "name": server.name if server else item.get("server_host", ""),
                "address": item.get("server_host", ""),
            },
            "status": status,
            "last_checked_at": state.last_checked_at.isoformat() + "Z" if state and state.last_checked_at else None,
            "last_error_code": (state.last_error_code or "") if state else "",
            "last_error": _safe_error(state.last_error) if state else "",
            "consecutive_failures": int(state.consecutive_failures or 0) if state else 0,
            "active_incident_id": str(state.active_incident_id) if state and state.active_incident_id else None,
            "silenced_until": silenced[item["key"]].isoformat() + "Z" if item["key"] in silenced else None,
        })
    return result


def _incident_rows(hours: int, active_only: bool, limit: int) -> list[dict]:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    catalog, plaza_servers = _load_plaza_items()
    plaza_context = {}
    for item in catalog:
        server = plaza_servers.get(item.get("server_host"))
        plaza_context[item["key"]] = {
            "name": item.get("name") or item["key"],
            "server_id": str(server.id) if server else None,
            "server_name": server.name if server else item.get("server_host", ""),
        }
    with get_db() as db:
        servers = {row.id: row for row in db.query(Server).all()}
        rules = {row.id: row for row in db.query(AlertRule).all()}
        plaza_query = db.query(PlazaHealthIncident).filter(PlazaHealthIncident.opened_at >= cutoff)
        alert_query = db.query(AlertEvent).filter(AlertEvent.created_at >= cutoff)
        if active_only:
            plaza_query = plaza_query.filter(PlazaHealthIncident.status.in_(_ACTIVE_INCIDENT_STATES))
            alert_query = alert_query.filter(AlertEvent.status.in_(_ACTIVE_ALERT_STATES))
        plaza_rows = plaza_query.order_by(PlazaHealthIncident.opened_at.desc()).limit(limit).all()
        alert_rows = alert_query.order_by(AlertEvent.created_at.desc()).limit(limit).all()
        items = [{
            "id": str(row.id),
            "kind": "service_health",
            "status": row.status,
            "service_key": row.plaza_key,
            "server_id": plaza_context.get(row.plaza_key, {}).get("server_id"),
            "server_name": plaza_context.get(row.plaza_key, {}).get("server_name", ""),
            "title": f"服务 {plaza_context.get(row.plaza_key, {}).get('name', row.plaza_key)} 健康异常",
            "value": None,
            "error_code": row.last_error_code or "network_error",
            "error": _safe_error(row.last_error),
            "opened_at": row.opened_at.isoformat() + "Z",
            "resolved_at": row.resolved_at.isoformat() + "Z" if row.resolved_at else None,
        } for row in plaza_rows]
        items.extend({
            "id": str(row.id),
            "kind": "metric_alert",
            "status": row.status,
            "service_key": None,
            "server_id": str(row.server_id),
            "server_name": servers[row.server_id].name if row.server_id in servers else "",
            "title": rules[row.rule_id].name if row.rule_id in rules else "监控告警",
            "value": row.current_value,
            "error_code": "",
            "error": "",
            "opened_at": (row.fired_at or row.created_at).isoformat() + "Z",
            "resolved_at": row.recovered_at.isoformat() + "Z" if row.recovered_at else None,
        } for row in alert_rows)
    items.sort(key=lambda item: item["opened_at"], reverse=True)
    return items[:limit]


@router.get("/capabilities")
def ai_capabilities():
    return _envelope({
        "contract": "OpsCenter AI Context API",
        "read_only": True,
        "authentication": "Authorization: Bearer <OpsCenter read/write API key>",
        "freshness": "Responses include generated_at; metric samples include age_seconds and stale.",
        "endpoints": [
            {"path": "/api/v2/ai/capabilities", "purpose": "Contract, authentication and safety guarantees"},
            {"path": "/api/v2/ai/summary", "purpose": "Compact overall operational posture"},
            {"path": "/api/v2/ai/hosts", "purpose": "Hosts, agents and latest persisted metrics"},
            {"path": "/api/v2/ai/services", "purpose": "Visible Web services and persisted health state"},
            {"path": "/api/v2/ai/incidents", "purpose": "Service incidents and metric alerts"},
            {"path": "/api/v2/ai/snapshot", "purpose": "Bounded combined context for one AI call"},
        ],
        "guarantees": [
            "No endpoint executes SSH, Docker, process signals, scans or network probes.",
            "No password, private key, agent token, API-key hash or encrypted credential is returned.",
            "Lists are bounded and schema_version changes only for incompatible contracts.",
        ],
    })


@router.get("/hosts")
def ai_hosts():
    return _envelope(_host_rows())


@router.get("/services")
def ai_services():
    return _envelope(_service_rows())


@router.get("/incidents")
def ai_incidents(
    hours: int = Query(24, ge=1, le=24 * 365),
    active_only: bool = True,
    limit: int = Query(100, ge=1, le=500),
):
    return _envelope(_incident_rows(hours, active_only, limit))


@router.get("/summary")
def ai_summary():
    hosts = _host_rows()
    services = _service_rows()
    incidents = _incident_rows(24, True, 200)
    stale_hosts = sum(1 for row in hosts if not row["latest_metric_at"] or all(
        metric is None or metric["stale"] for metric in row["metrics"].values()
    ))
    return _envelope({
        "posture": "critical" if any(row["status"] == "down" for row in services) else (
            "degraded" if incidents or stale_hosts else "healthy"
        ),
        "hosts": {
            "total": len(hosts),
            "online": sum(row["status"] == "online" for row in hosts),
            "agent_running": sum(row["agent"]["status"] == "running" for row in hosts),
            "metrics_stale": stale_hosts,
        },
        "services": {
            "total": len(services),
            "up": sum(row["status"] == "up" for row in services),
            "down": sum(row["status"] == "down" for row in services),
            "degraded": sum(row["status"] == "degraded" for row in services),
            "unknown": sum(row["status"] == "unknown" for row in services),
            "disabled": sum(row["status"] == "disabled" for row in services),
        },
        "active_incidents": {
            "total": len(incidents),
            "service_health": sum(row["kind"] == "service_health" for row in incidents),
            "metric_alert": sum(row["kind"] == "metric_alert" for row in incidents),
        },
    })


@router.get("/snapshot")
def ai_snapshot(
    incident_hours: int = Query(24, ge=1, le=24 * 30),
    incident_limit: int = Query(50, ge=1, le=200),
):
    hosts = _host_rows()
    services = _service_rows()
    incidents = _incident_rows(incident_hours, True, incident_limit)
    return _envelope({
        "hosts": hosts,
        "services": services,
        "active_incidents": incidents,
    }, warnings=[
        "This is a persisted snapshot, not a command result or synchronous live probe.",
    ])
