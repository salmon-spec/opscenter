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
from pydantic import BaseModel, Field, field_validator, model_validator
import math
import engine
import center
from templates import METRICS

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
    return [event for event in events if event.get('status') in {'pending', 'firing', 'acked'}
            or ((created := _parse_time(event.get("created_at"))) and created >= cutoff)]


def _snapshot(required: bool = False):
    global _last_refresh_error
    store = _store()
    cached = store.read()
    now = datetime.now(timezone.utc)
    sync_enabled = os.getenv("ALERTS_SYNC_ENABLED", "false").lower() in {"1", "true", "yes"}
    if cached:
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
    collected_at = _parse_time(data.get('meta', {}).get('last_evaluated_at')) or collected_at
    if any(value.get('status') in {'unavailable', 'degraded'} for value in data.get('meta', {}).get('sources', {}).values()):
        error = '部分告警数据源暂不可用'
    events = _events_since(data["events"], 30)
    counts = event_counts(events)
    counts["rules_total"] = sum(not rule.get('archived') for rule in data["rules"])
    counts["rules_enabled"] = sum(bool(rule.get("enabled")) and not rule.get('archived') for rule in data["rules"])
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
            "severity": event.get('severity', 'warning'),
            "title": event.get("rule_name") or "告警事件",
            "summary": f"{event.get('server_name') or '未知主机'} · {event.get('status', 'unknown')} · {event.get('current_value') or '-'}",
            "observed_at": event.get("fired_at") or event.get("created_at") or collected_at,
            "url": "/alerts",
        }
        for event in events
        if engine.normalize(event)["condition_status"] == "firing" and event.get("handling_status") == "unacked" and event.get("id")
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


def evaluate_once():
    return center.evaluate(_store, _snapshot, _module_get)


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
    version="0.6.0",
    summary_provider=summary_provider,
    findings_provider=findings_provider,
    readiness_provider=readiness_provider,
    capabilities=["summary:read", "findings:read", "events:read", "events:ack", "events:comment", "events:close", "rules:read", "rules:write", "silences:read", "silences:write", "policies:manage", "deliveries:read", "analysis:read"],
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
    severity: str = Field(default="warning", pattern="^(critical|warning|info)$")
    resource_id: str | None = Field(default=None, max_length=300)
    recovery_sec: int = Field(default=0, ge=0, le=86400)
    recovery_threshold: str | None = Field(default=None, max_length=120)
    policy_ids: list[str] = Field(default_factory=list, max_length=30)
    inhibit_on_host_down: bool = False
    runbook_url: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_condition(self):
        if self.metric not in METRICS:
            raise ValueError("请选择支持的告警指标")
        if self.value_type != METRICS[self.metric]['value_type']:
            raise ValueError("值类型与指标不匹配")
        if self.value_type == 'numeric':
            for value in (self.threshold, self.recovery_threshold):
                if value is not None and not math.isfinite(float(value)):
                    raise ValueError("阈值必须为有限数值")
            if self.recovery_threshold is not None:
                if self.operator in ('==', '!='):
                    raise ValueError('等于和不等于条件无需独立恢复阈值')
                if self.operator in ('>', '>=') and float(self.recovery_threshold) > float(self.threshold):
                    raise ValueError("恢复阈值不能高于触发阈值")
                if self.operator in ('<', '<=') and float(self.recovery_threshold) < float(self.threshold):
                    raise ValueError("恢复阈值不能低于触发阈值")
        elif self.operator not in ('==', '!='):
            raise ValueError("文本状态仅支持等于和不等于")
        if self.runbook_url and not self.runbook_url.startswith(('https://', 'http://')):
            raise ValueError("处置文档必须为 HTTP 或 HTTPS 地址")
        return self
    enabled: bool = True


class SilencePayload(BaseModel):
    rule_id: str | None = None
    server_id: str | None = None
    starts_at: str
    ends_at: str
    reason: str = Field(default="", max_length=500)
    resource_id: str | None = Field(default=None, max_length=300)
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    daily_start: int = Field(default=0, ge=0, le=1439)
    daily_end: int = Field(default=1440, ge=1, le=1440)

    @model_validator(mode="after")
    def validate_schedule(self):
        if any(day < 0 or day > 6 for day in self.weekdays) or (self.weekdays and self.daily_start == self.daily_end):
            raise ValueError("维护时间无效")
        return self


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
    _snapshot(required=True)
    data = _store().mutate(fn, actor, action, target)
    global _last_refresh_error
    _last_refresh_error = ""
    return data


@app.get("/api/v1/events")
def list_events(status: str | None = None, server_id: str | None = None, days: int = Query(default=7, ge=1, le=365)):
    data, _, _ = _snapshot(required=True)
    events = _events_since(data["events"], days)
    return [engine.normalize(dict(event)) for event in events if (not status or (engine.normalize(event)['handling_status'] == 'acked' if status == 'acked' else event.get("status") == status))
            and (not server_id or event.get("server_id") == server_id)]


@app.get("/api/v1/rules")
def list_rules():
    return [row for row in _snapshot(required=True)[0]["rules"] if not row.get("archived")]


@app.get("/api/v1/silences")
def list_silences(active: int | None = Query(default=None, ge=0, le=1)):
    now = datetime.now(timezone.utc)
    rows = []
    data = _snapshot(required=True)[0]
    rule_names = {rule['id']: rule.get('name') for rule in data['rules'] if rule.get('id')}
    for row in data["silences"]:
        starts, ends = _parse_time(row.get("starts_at")), _parse_time(row.get("ends_at"))
        item = {**row, "active": bool(starts and ends and starts <= now < ends and not row.get("ended_at"))}
        item['rule_name'] = rule_names.get(row.get('rule_id')) or row.get('rule_name') or ('指定规则' if row.get('rule_id') else None)
        if active is None or item["active"] == bool(active):
            rows.append(item)
    return rows


@app.post("/api/v1/events/{event_id}/ack")
def ack_event(event_id: str, user=Depends(app.state.require_editor)):
    def update(data):
        event = next((row for row in data["events"] if row.get("id") == event_id), None)
        if not event:
            raise HTTPException(404, "告警事件不存在")
        engine.normalize(event)
        if event['condition_status'] != 'firing':
            raise HTTPException(409, "只能确认正在触发的告警")
        if event['handling_status'] != 'unacked':
            return False
        event.update(handling_status="acked", acked_by=user["username"], acked_at=datetime.now(timezone.utc).isoformat())
        engine.stamp(event, 'acked', datetime.now(timezone.utc), user['username'])
    _mutate(user["username"], "alert.event.ack", event_id, update)
    return {"ok": True, "status": "acked"}


@app.post("/api/v1/rules", status_code=201)
def create_rule(payload: RulePayload, user=Depends(app.state.require_editor)):
    row = {**payload.model_dump(), "id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat()}
    def add(data):
        if any(not any(policy['id'] == key and not policy.get('archived') for policy in data.get('policies', [])) for key in row['policy_ids']):
            raise HTTPException(422, '所选通知策略已不存在')
        data['rules'].append(row)
    _mutate(user["username"], "alert.rule.create", row["id"], add)
    return {"id": row["id"], "name": row["name"]}


@app.put("/api/v1/rules/{rule_id}")
def update_rule(rule_id: str, payload: RulePayload, user=Depends(app.state.require_editor)):
    values = payload.model_dump()
    def update(data):
        row = next((item for item in data["rules"] if item.get("id") == rule_id), None)
        if not row:
            raise HTTPException(404, "告警规则不存在")
        if row.get('archived'):
            raise HTTPException(409, '该规则已归档')
        if any(not any(policy['id'] == key and not policy.get('archived') for policy in data.get('policies', [])) for key in values['policy_ids']):
            raise HTTPException(422, '所选通知策略已不存在')
        row.update(values)
    _mutate(user["username"], "alert.rule.update", rule_id, update)
    return {"id": rule_id, "name": values["name"]}


@app.delete("/api/v1/rules/{rule_id}")
def delete_rule(rule_id: str, user=Depends(app.state.require_editor)):
    def remove(data):
        if not any(row.get("id") == rule_id for row in data["rules"]):
            raise HTTPException(404, "告警规则不存在")
        rule = next(row for row in data['rules'] if row['id'] == rule_id)
        rule.update(archived=True, enabled=False, archived_at=datetime.now(timezone.utc).isoformat())
        for event in data['events']:
            engine.normalize(event)
            if event.get('rule_id') == rule_id and event['condition_status'] in {'pending', 'firing'}:
                event.update(status='cancelled', condition_status='cancelled', ended_at=datetime.now(timezone.utc).isoformat())
                engine.stamp(event, 'rule_archived', datetime.now(timezone.utc), user['username'])
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
        row = next(row for row in data['silences'] if row['id'] == silence_id)
        row['ended_at'] = datetime.now(timezone.utc).isoformat()
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


center.register(app, _store, _snapshot, _mutate, lambda *args: _module_get(*args))
