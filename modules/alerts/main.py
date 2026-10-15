from __future__ import annotations

import json
import ipaddress
import os
import socket
import ssl
import threading
import uuid
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from runtime.auth import ModuleAuth
from runtime.app import create_module_app
from storage import AlertsStore

logger = logging.getLogger("opscenter.alerts")
_refresh_seconds = int(os.getenv("ALERTS_REFRESH_SECONDS", "30"))
_last_refresh_error = ""
_storage_ready = False
_storage_lock = threading.Lock()
_evaluation_stop = threading.Event()
_evaluation_thread = None
_certificate_thread = None
_certificate_interval = max(300, int(os.getenv("ALERTS_CERT_SCAN_INTERVAL_SECONDS", "21600")))
_fields = {
    "events": ("id", "rule_id", "rule_name", "server_id", "server_name", "status", "current_value", "first_breached_at", "fired_at", "recovered_at", "acked_by", "acked_at", "created_at"),
    "rules": ("id", "name", "server_id", "server_name", "metric", "value_type", "operator", "threshold", "duration_sec", "cooldown_sec", "enabled", "created_at"),
    "silences": ("id", "rule_id", "rule_name", "server_id", "server_name", "starts_at", "ends_at", "reason", "active", "created_at"),
}


def get(path: str, query: dict | None = None):
    url = os.getenv("ALERTS_SOURCE_URL", "http://backend:9091/api/v2").rstrip("/") + path
    if query:
        url += "?" + urlencode(query)
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=8) as response:
            return json.load(response)
    except (HTTPError, TimeoutError, URLError, ValueError) as error:
        raise HTTPException(502, "告警数据源暂不可用") from error


def _module_get(url: str, token_file: str, path: str) -> dict | None:
    try:
        token = Path(token_file).read_text(encoding="utf-8").strip()
        if not token:
            return None
        request = Request(url.rstrip("/") + path,
                          headers={"Accept": "application/json", "X-OpsCenter-Module-Token": token})
        with urlopen(request, timeout=5) as response:
            value = json.load(response)
        return value if isinstance(value, dict) else None
    except (OSError, HTTPError, TimeoutError, URLError, ValueError):
        return None


def _module_findings(url: str, token_file: str) -> dict | None:
    value = _module_get(url, token_file, "/api/v1/internal/findings")
    return value if value is None or (value.get("status") in {"ok", "degraded", "unavailable"}
                                      and isinstance(value.get("items"), list)) else None


def public_rows(rows: list[dict], fields: tuple[str, ...]):
    if not isinstance(rows, list):
        raise HTTPException(502, "告警数据格式异常")
    return [{key: row[key] for key in fields if key in row} for row in rows if isinstance(row, dict)]


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def check_certificate(domain: str, port: int = 443, timeout: float = 6.0) -> dict:
    """Check a public TLS endpoint without allowing private-network probes."""
    addresses = socket.getaddrinfo(domain, port, type=socket.SOCK_STREAM)
    targets = list(dict.fromkeys((family, sockaddr) for family, _, _, _, sockaddr in addresses))
    if not targets or any(not ipaddress.ip_address(address[0]).is_global for _, address in targets):
        raise ValueError("证书目标必须解析到公网地址")
    context = ssl.create_default_context()
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    last_error = None
    for family, address in targets:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect(address)
                with context.wrap_socket(sock, server_hostname=domain) as tls:
                    cert = tls.getpeercert()
            expires_at = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), timezone.utc)
            issuer = next((value for group in cert.get("issuer", ()) for key, value in group if key == "commonName"), "")
            return {"days_left": int((expires_at - datetime.now(timezone.utc)).total_seconds() / 86400),
                    "not_after": expires_at.isoformat(), "issuer": issuer}
        except (OSError, ssl.SSLError, KeyError, ValueError) as error:
            last_error = error
    raise last_error or OSError("证书探测失败")


def scan_certificates() -> int:
    checks = _store().read_certificates()
    updated_at = datetime.now(timezone.utc).isoformat()
    results = {}
    for check in checks:
        if not check.get("enabled", True):
            continue
        try:
            result = {**check_certificate(check["domain"], int(check["port"])), "last_error": None}
        except Exception as error:
            result = {"days_left": None, "not_after": None, "issuer": None, "last_error": str(error)[:250]}
        results[check["id"]] = {**result, "updated_at": updated_at}

    def apply_results(current):
        for check in current:
            if check.get("enabled", True) and check.get("id") in results:
                check.update(results[check["id"]])

    _store().mutate_certificates(apply_results)
    return len(results)


def certificate_provider():
    return _store().read_certificates()


def _events_since(events: list[dict], days: int):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return [event for event in events if (created := _parse_time(event.get("created_at"))) and created >= cutoff]


def _snapshot(required: bool = False):
    global _last_refresh_error
    store = _store()
    cached = store.read()
    now = datetime.now(timezone.utc)
    sync_enabled = os.getenv("ALERTS_SYNC_ENABLED", "true").lower() in {"1", "true", "yes"}
    if cached and (not sync_enabled or (now - cached[1]).total_seconds() < _refresh_seconds):
        return cached[0], cached[1], _last_refresh_error
    if not sync_enabled:
        return {"events": [], "rules": [], "silences": []}, now, ""
    try:
        payload = {
            "events": public_rows(get("/alert-events", {"days": 365}), _fields["events"]),
            "rules": public_rows(get("/alert-rules"), _fields["rules"]),
            "silences": public_rows(get("/alert-silences"), _fields["silences"]),
        }
        collected_at = datetime.now(timezone.utc)
        store.save(payload, collected_at)
        _last_refresh_error = ""
        return payload, collected_at, ""
    except HTTPException:
        _last_refresh_error = "旧告警数据源暂不可用，当前使用模块数据库中的最近快照"
        if cached:
            return cached[0], cached[1], _last_refresh_error
        if required:
            raise
        return {"events": [], "rules": [], "silences": []}, now, _last_refresh_error


def _status(collected_at: datetime, error: str):
    age = max(0, int((datetime.now(timezone.utc) - collected_at).total_seconds()))
    stale = age > 300
    return age, "degraded" if error or stale else "ok", error or ("告警快照超过 5 分钟未刷新" if stale else None)


def event_counts(events: list[dict]) -> dict[str, int]:
    counts = {"events_total": len(events), "pending": 0, "firing": 0, "acked": 0, "recovered": 0}
    for event in events:
        status = event.get("status")
        if status in counts and status != "events_total":
            counts[status] += 1
    return counts


def summary_provider():
    data, collected_at, error = _snapshot()
    events = _events_since(data["events"], 30)
    counts = event_counts(events)
    counts["rules_total"] = len(data["rules"])
    counts["rules_enabled"] = sum(bool(rule.get("enabled")) for rule in data["rules"])
    age, status, error = _status(collected_at, error)
    return {"status": status, "collected_at": collected_at, "stale_after_seconds": 300,
            "data": {**counts, "cache_age_seconds": age}, "error": error}


def findings_provider():
    data, collected_at, error = _snapshot()
    events = _events_since(data["events"], 30)
    items = [
        {
            "id": f"alert-event:{event['id']}",
            "resource_id": event.get("server_id"),
            "severity": "critical" if event.get("status") == "firing" else "warning",
            "title": event.get("rule_name") or "告警事件",
            "summary": f"{event.get('server_name') or '未知主机'} · {event.get('status', 'unknown')} · {event.get('current_value') or '-'}",
            "observed_at": event.get("fired_at") or event.get("created_at") or collected_at,
            "url": "/alerts",
        }
        for event in events
        if event.get("status") in {"pending", "firing"} and event.get("id")
    ][:1000]
    sources = (
        ("HOST_INVENTORY_INTERNAL_URL", "HOST_INVENTORY_INTERNAL_TOKEN_FILE"),
        ("HOST_MONITOR_INTERNAL_URL", "HOST_MONITOR_INTERNAL_TOKEN_FILE"),
    )
    source_errors = []
    for url_env, token_env in sources:
        token_file = os.getenv(token_env, "")
        if not token_file:
            continue
        module = _module_findings(os.getenv(url_env, ""), token_file)
        if module is None:
            source_errors.append(url_env.removesuffix("_INTERNAL_URL").lower().replace("_", "-") + " 数据源暂不可用")
        else:
            items.extend(module["items"][:1000])
            if module.get("status") != "ok":
                source_errors.append(url_env.removesuffix("_INTERNAL_URL").lower().replace("_", "-") + " 数据已降级")
    _, status, error = _status(collected_at, error)
    if source_errors:
        status, error = "degraded", "; ".join(source_errors)
    return {"status": status, "collected_at": collected_at, "items": items[:1000], "error": error}


def readiness_provider():
    store = _store()
    store.ping()


def _store():
    global _storage_ready
    store = AlertsStore()
    if not _storage_ready:
        with _storage_lock:
            if not _storage_ready:
                store.migrate()
                _storage_ready = True
    return store


def _breaches(rule: dict, value) -> bool:
    if value is None:
        return False
    try:
        left, right = (float(value), float(rule["threshold"])) if rule.get("value_type") == "numeric" else (str(value), str(rule["threshold"]))
        operation = {">": lambda: left > right, "<": lambda: left < right,
                     ">=": lambda: left >= right, "<=": lambda: left <= right,
                     "==": lambda: left == right, "!=": lambda: left != right}.get(rule.get("operator"))
        return operation() if operation else False
    except (ValueError, TypeError, KeyError):
        return False


def evaluate_once():
    """Evaluate supported host metrics; missing/expired sources never create incidents."""
    inventory = _module_get(os.getenv("HOST_INVENTORY_INTERNAL_URL", ""),
                            os.getenv("HOST_INVENTORY_INTERNAL_TOKEN_FILE", ""), "/api/v1/internal/hosts")
    metrics = _module_get(os.getenv("HOST_MONITOR_INTERNAL_URL", ""),
                          os.getenv("HOST_MONITOR_INTERNAL_TOKEN_FILE", ""), "/api/v1/internal/metrics/latest")
    backups = _module_get(os.getenv("PVE_BACKUP_INTERNAL_URL", ""),
                          os.getenv("PVE_BACKUP_INTERNAL_TOKEN_FILE", ""), "/api/v1/internal/summary")
    inventory_at = _parse_time(inventory.get("collected_at")) if inventory else None
    now = datetime.now(timezone.utc)
    inventory_ready = bool(inventory and inventory.get("status") in {"ok", "degraded"}
                           and not inventory.get("error") and inventory_at
                           and (now - inventory_at).total_seconds() <= 300)
    data, _, _ = _snapshot(required=True)
    metric_hosts = {str(item.get("id")): item for item in (metrics or {}).get("items", [])}
    hosts = inventory.get("items", []) if inventory_ready else []
    certificates = _store().read_certificates()
    backup_data = backups.get("data", {}) if backups and backups.get("status") in {"ok", "degraded"} else {}
    backup_at = _parse_time(backups.get("collected_at")) if backups else None
    backup_guests = backup_data.get("guests", []) if (
        backup_at and (now - backup_at).total_seconds() <= 300
    ) else []
    changed = False
    for rule in data["rules"]:
        if not rule.get("enabled"):
            continue
        if rule.get("metric") == "cert_days_left":
            targets = []
            for certificate in certificates:
                checked_at = _parse_time(certificate.get("updated_at"))
                if (not certificate.get("enabled", True) or certificate.get("last_error")
                        or certificate.get("days_left") is None or not checked_at
                        or (now - checked_at).total_seconds() > 28800
                        or (rule.get("server_id") and str(certificate.get("server_id")) != str(rule["server_id"]))):
                    continue
                targets.append((str(certificate.get("server_id") or f"cert:{certificate.get('id')}"),
                                certificate.get("domain"), certificate["days_left"]))
        elif rule.get("metric") == "backup_age":
            targets = []
            for guest in backup_guests:
                if rule.get("server_id") and str(guest.get("vmid")) != str(rule["server_id"]):
                    continue
                coverage = guest.get("coverage")
                age_hours = guest.get("backup_age_days")
                if coverage == "missing":
                    value = 8760
                elif coverage in {"current", "stale"} and age_hours is not None:
                    value = float(age_hours) * 24
                else:
                    continue
                targets.append((str(guest.get("vmid")), guest.get("name"), value))
        elif inventory_ready:
            targets = [(str(host.get("id") or ""), host.get("name"), host)
                       for host in hosts if not rule.get("server_id") or str(host.get("id")) == str(rule["server_id"])]
        else:
            continue
        for host_id, host_name, host in targets:
            if rule.get("metric") == "cert_days_left":
                value = host
            elif rule.get("metric") == "backup_age":
                value = host
            else:
                sample = metric_hosts.get(host_id, {})
                sample_at = _parse_time(sample.get("last_sample_at"))
                if rule.get("metric") == "server_status":
                    value = host.get("status")
                elif rule.get("metric") == "agent_status":
                    value = host.get("agent_status")
                elif sample_at and (now - sample_at).total_seconds() <= 120:
                    value = (sample.get("metrics") or {}).get(rule.get("metric"))
                else:
                    value = None
            if value is None:
                continue
            silence = any(
                (not item.get("rule_id") or item.get("rule_id") == rule.get("id"))
                and (not item.get("server_id") or str(item.get("server_id")) == host_id)
                and (start := _parse_time(item.get("starts_at"))) and (end := _parse_time(item.get("ends_at")))
                and start <= now < end for item in data["silences"]
            )
            if silence:
                continue
            event = next((item for item in reversed(data["events"])
                          if item.get("rule_id") == rule.get("id") and str(item.get("server_id")) == host_id
                          and item.get("status") in {"pending", "firing", "acked"}), None)
            breach = _breaches(rule, value)
            if breach and event is None:
                recent = next((item for item in reversed(data["events"])
                               if item.get("rule_id") == rule.get("id") and str(item.get("server_id")) == host_id
                               and item.get("status") == "recovered"), None)
                recovered_at = _parse_time(recent.get("recovered_at")) if recent else None
                if recovered_at and (now - recovered_at).total_seconds() < int(rule.get("cooldown_sec", 0)):
                    continue
                event = {"id": str(uuid.uuid4()), "rule_id": rule["id"], "rule_name": rule["name"],
                         "server_id": host_id, "server_name": host_name, "status": "pending",
                         "current_value": str(value), "first_breached_at": now.isoformat(), "created_at": now.isoformat()}
                data["events"].append(event)
                changed = True
            elif event and breach:
                previous = (event.get("current_value"), event.get("status"))
                event["current_value"] = str(value)
                if event["status"] == "pending" and (now - (_parse_time(event.get("first_breached_at") or event.get("created_at")) or now)).total_seconds() >= int(rule.get("duration_sec", 0)):
                    event.update(status="firing", fired_at=now.isoformat())
                changed = changed or previous != (event.get("current_value"), event.get("status"))
            elif event and not breach:
                if event["status"] == "pending":
                    data["events"].remove(event)
                else:
                    event.update(status="recovered", recovered_at=now.isoformat())
                changed = True
    if changed:
        _store().mutate(data, "system:alert-evaluator", "alert.events.evaluate", "batch")
    return changed


def _evaluate_loop():
    while not _evaluation_stop.wait(60):
        try:
            evaluate_once()
        except Exception as error:
            logger.warning("alert evaluation failed: %s", type(error).__name__)


def _certificate_loop():
    while not _evaluation_stop.wait(_certificate_interval):
        try:
            scan_certificates()
        except Exception as error:
            logger.warning("certificate scan failed: %s", type(error).__name__)


app = create_module_app(
    key="alerts",
    version="0.5.2",
    summary_provider=summary_provider,
    findings_provider=findings_provider,
    readiness_provider=readiness_provider,
    capabilities=["summary:read", "findings:read", "events:read", "events:ack", "rules:read", "rules:write", "silences:read", "silences:write"],
)
auth = ModuleAuth(AlertsStore)
auth.register(app)


@app.on_event("startup")
def start_evaluator():
    global _evaluation_thread, _certificate_thread
    if _evaluation_thread is None:
        _evaluation_stop.clear()
        _evaluation_thread = threading.Thread(target=_evaluate_loop, name="alerts-evaluator", daemon=True)
        _evaluation_thread.start()
    if _certificate_thread is None:
        _certificate_thread = threading.Thread(target=_certificate_loop, name="alerts-certificates", daemon=True)
        _certificate_thread.start()


@app.on_event("shutdown")
def stop_evaluator():
    global _evaluation_thread, _certificate_thread
    _evaluation_stop.set()
    if _evaluation_thread:
        _evaluation_thread.join(timeout=3)
        _evaluation_thread = None
    if _certificate_thread:
        _certificate_thread.join(timeout=3)
        _certificate_thread = None


@app.on_event("startup")
def initialize_storage():
    _store().migrate()
    auth.ensure_schema()


class RulePayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    server_id: str | None = None
    metric: str = Field(min_length=1, max_length=60)
    value_type: str = Field(default="numeric", pattern="^(numeric|string)$")
    operator: str = Field(default=">", pattern="^(>|<|>=|<=|==|!=)$")
    threshold: str = Field(min_length=1, max_length=120)
    duration_sec: int = Field(default=60, ge=0, le=86400)
    cooldown_sec: int = Field(default=300, ge=0, le=604800)
    enabled: bool = True


class SilencePayload(BaseModel):
    rule_id: str | None = None
    server_id: str | None = None
    starts_at: str
    ends_at: str
    reason: str = Field(default="", max_length=500)


class CertificatePayload(BaseModel):
    domain: str = Field(min_length=1, max_length=253, pattern=r"^[A-Za-z0-9.-]+$")
    port: int = Field(default=443, ge=1, le=65535)
    server_id: str | None = None
    enabled: bool = True

    @field_validator("domain")
    @classmethod
    def normalize_domain(cls, value):
        domain = value.rstrip(".").lower()
        labels = domain.split(".")
        if not domain or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
                             for label in labels):
            raise ValueError("域名格式无效")
        return domain


def _mutate(actor: str, action: str, target: str, fn):
    data, _, _ = _snapshot(required=True)
    fn(data)
    _store().mutate(data, actor, action, target)
    global _last_refresh_error
    _last_refresh_error = ""
    return data


@app.get("/api/v1/events")
def list_events(status: str | None = None, server_id: str | None = None, days: int = Query(default=7, ge=1, le=365)):
    data, _, _ = _snapshot(required=True)
    events = _events_since(data["events"], days)
    return [event for event in events if (not status or event.get("status") == status)
            and (not server_id or event.get("server_id") == server_id)]


@app.get("/api/v1/rules")
def list_rules():
    return _snapshot(required=True)[0]["rules"]


@app.get("/api/v1/silences")
def list_silences(active: int | None = Query(default=None, ge=0, le=1)):
    now = datetime.now(timezone.utc)
    rows = []
    for row in _snapshot(required=True)[0]["silences"]:
        starts, ends = _parse_time(row.get("starts_at")), _parse_time(row.get("ends_at"))
        item = {**row, "active": bool(starts and ends and starts <= now < ends)}
        if active is None or item["active"] == bool(active):
            rows.append(item)
    return rows


@app.post("/api/v1/events/{event_id}/ack")
def ack_event(event_id: str, user=Depends(app.state.require_editor)):
    def update(data):
        event = next((row for row in data["events"] if row.get("id") == event_id), None)
        if not event:
            raise HTTPException(404, "告警事件不存在")
        event.update(status="acked", acked_by=user["username"], acked_at=datetime.now(timezone.utc).isoformat())
    _mutate(user["username"], "alert.event.ack", event_id, update)
    return {"ok": True, "status": "acked"}


@app.post("/api/v1/rules", status_code=201)
def create_rule(payload: RulePayload, user=Depends(app.state.require_editor)):
    row = {**payload.model_dump(), "id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat()}
    _mutate(user["username"], "alert.rule.create", row["id"], lambda data: data["rules"].append(row))
    return {"id": row["id"], "name": row["name"]}


@app.put("/api/v1/rules/{rule_id}")
def update_rule(rule_id: str, payload: RulePayload, user=Depends(app.state.require_editor)):
    values = payload.model_dump()
    def update(data):
        row = next((item for item in data["rules"] if item.get("id") == rule_id), None)
        if not row:
            raise HTTPException(404, "告警规则不存在")
        row.update(values)
    _mutate(user["username"], "alert.rule.update", rule_id, update)
    return {"id": rule_id, "name": values["name"]}


@app.delete("/api/v1/rules/{rule_id}")
def delete_rule(rule_id: str, user=Depends(app.state.require_editor)):
    def remove(data):
        if not any(row.get("id") == rule_id for row in data["rules"]):
            raise HTTPException(404, "告警规则不存在")
        data["rules"] = [row for row in data["rules"] if row.get("id") != rule_id]
        data["events"] = [row for row in data["events"] if row.get("rule_id") != rule_id]
        data["silences"] = [row for row in data["silences"] if row.get("rule_id") != rule_id]
    _mutate(user["username"], "alert.rule.delete", rule_id, remove)
    return {"ok": True}


@app.post("/api/v1/silences", status_code=201)
def create_silence(payload: SilencePayload, user=Depends(app.state.require_editor)):
    starts, ends = _parse_time(payload.starts_at), _parse_time(payload.ends_at)
    if not starts or not ends or ends <= starts:
        raise HTTPException(422, "静默时间范围无效")
    row = {**payload.model_dump(), "id": str(uuid.uuid4()), "created_by": user["username"],
           "created_at": datetime.now(timezone.utc).isoformat(), "active": starts <= datetime.now(timezone.utc) < ends}
    def add(data):
        if row["rule_id"] and not any(rule.get("id") == row["rule_id"] for rule in data["rules"]):
            raise HTTPException(404, "告警规则不存在")
        data["silences"].append(row)
    _mutate(user["username"], "alert.silence.create", row["id"], add)
    return {"id": row["id"], "starts_at": starts.isoformat(), "ends_at": ends.isoformat()}


@app.delete("/api/v1/silences/{silence_id}")
def delete_silence(silence_id: str, user=Depends(app.state.require_editor)):
    def remove(data):
        if not any(row.get("id") == silence_id for row in data["silences"]):
            raise HTTPException(404, "静默记录不存在")
        data["silences"] = [row for row in data["silences"] if row.get("id") != silence_id]
    _mutate(user["username"], "alert.silence.delete", silence_id, remove)
    return {"ok": True}


@app.get("/api/v1/certificates")
def list_certificates():
    return certificate_provider()


@app.post("/api/v1/certificates", status_code=201)
def create_certificate(payload: CertificatePayload, user=Depends(app.state.require_editor)):
    row = {**payload.model_dump(), "id": str(uuid.uuid4()), "days_left": None,
           "not_after": None, "issuer": None, "last_error": None, "updated_at": None}
    def add(checks):
        if any(item.get("domain", "").lower() == row["domain"] and int(item.get("port", 443)) == row["port"] for item in checks):
            raise HTTPException(409, "该证书目标已存在")
        checks.append(row)
    _store().mutate_certificates(add, user["username"], "alert.certificate.create", row["id"])
    return {"id": row["id"], "domain": row["domain"]}


@app.delete("/api/v1/certificates/{certificate_id}")
def delete_certificate(certificate_id: str, user=Depends(app.state.require_editor)):
    def remove(checks):
        if not any(item.get("id") == certificate_id for item in checks):
            raise HTTPException(404, "证书检查项不存在")
        checks[:] = [item for item in checks if item.get("id") != certificate_id]
    _store().mutate_certificates(remove, user["username"], "alert.certificate.delete", certificate_id)
    return {"ok": True}


@app.post("/api/v1/certificates/scan")
def trigger_certificate_scan(user=Depends(app.state.require_editor)):
    return {"scanned": scan_certificates()}
