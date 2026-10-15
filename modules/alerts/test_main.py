from urllib.error import URLError
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
from main import app


class MemoryStore:
    def __init__(self):
        self.value = None
        self.certificates = []

    def read(self):
        return self.value

    def save(self, payload, collected_at):
        self.value = (payload, collected_at)

    def mutate(self, payload, actor, action, target):
        self.value = (payload, datetime.now(timezone.utc))

    def migrate(self):
        pass

    def ping(self):
        pass

    def read_certificates(self):
        return self.certificates

    def mutate_certificates(self, mutate, *args):
        mutate(self.certificates)


@pytest.fixture
def store(monkeypatch):
    value = MemoryStore()
    monkeypatch.setattr(main, "AlertsStore", lambda: value)
    monkeypatch.setattr(main, "_last_refresh_error", "")
    monkeypatch.setattr(main, "_storage_ready", False)
    return value


def test_read_routes_use_sanitized_module_snapshot(monkeypatch, store):
    calls = []
    now = datetime.now(timezone.utc)
    def source(path, query=None):
        calls.append((path, query))
        if path == "/alert-events":
            return [{"id": "id-1", "status": "firing", "created_at": now.isoformat()}]
        if path == "/alert-rules":
            return [{"id": "id-1"}]
        return [{"id": "id-1", "starts_at": (now - timedelta(hours=1)).isoformat(),
                 "ends_at": (now + timedelta(hours=1)).isoformat()}]
    monkeypatch.setattr(main, "get", source)
    client = TestClient(app)
    event = client.get("/api/v1/events?status=firing&days=30").json()[0]
    assert event["id"] == "id-1"
    assert event["status"] == "firing"
    assert client.get("/api/v1/rules").json() == [{"id": "id-1"}]
    silence = client.get("/api/v1/silences?active=1").json()[0]
    assert silence["id"] == "id-1"
    assert silence["active"] is True
    assert calls == [
        ("/alert-events", {"days": 365}),
        ("/alert-rules", None),
        ("/alert-silences", None),
    ]


def test_rule_and_silence_snapshots_exclude_webhook_and_creator(monkeypatch, store):
    monkeypatch.setattr(main, "get", lambda path, query=None: (
        [{"id": "r1", "name": "CPU", "notify_webhooks": ["https://secret.invalid/token"], "enabled": True}]
        if path == "/alert-rules" else
        [{"id": "s1", "reason": "maintenance", "created_by": "operator", "active": True}]
    ))
    client = TestClient(app)
    rule = client.get("/api/v1/rules").json()[0]
    silence = client.get("/api/v1/silences").json()[0]
    assert "notify_webhooks" not in rule
    assert "created_by" not in silence


def test_event_query_is_bounded_and_summary_counts_statuses(monkeypatch, store):
    client = TestClient(app)
    assert client.get("/api/v1/events?days=900").status_code == 422
    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(main, "get", lambda path, query=None: (
        [{"status": "firing", "created_at": now}, {"status": "pending", "created_at": now}, {"status": "recovered", "created_at": now}]
        if path == "/alert-events" else [{"enabled": True}, {"enabled": False}]
    ))
    result = client.get("/api/v1/summary").json()
    assert {key: value for key, value in result["data"].items() if key != "cache_age_seconds"} == {
        "events_total": 3, "pending": 1, "firing": 1, "acked": 0, "recovered": 1,
        "rules_total": 2, "rules_enabled": 1,
    }


def test_findings_have_stable_ids_and_writes_require_auth(monkeypatch, store):
    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(main, "get", lambda *args, **kwargs: [
        {"id": "event-1", "status": "firing", "server_id": "host-1", "rule_name": "CPU", "fired_at": now, "created_at": now},
        {"id": "event-2", "status": "recovered", "created_at": now},
    ])
    client = TestClient(app)
    findings = client.get("/api/v1/findings").json()["items"]
    assert len(findings) == 1
    assert findings[0]["id"] == "alert-event:event-1"
    assert client.post("/api/v1/events/event-1/ack").status_code == 401


def test_findings_pull_host_module_findings_with_internal_status(monkeypatch, store, tmp_path):
    now = datetime.now(timezone.utc)
    store.save({"events": [], "rules": [], "silences": []}, now)
    token_path = tmp_path / "inventory-token"
    token_path.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("HOST_INVENTORY_INTERNAL_URL", "http://module-host-inventory:8080")
    monkeypatch.setenv("HOST_INVENTORY_INTERNAL_TOKEN_FILE", str(token_path))
    monkeypatch.delenv("HOST_MONITOR_INTERNAL_TOKEN_FILE", raising=False)
    item = {"id": "host:one:health", "resource_id": "one", "severity": "warning",
            "title": "主机异常", "observed_at": now.isoformat()}
    monkeypatch.setattr(main, "_module_findings", lambda url, path: {"status": "degraded", "items": [item]})

    result = main.findings_provider()

    assert result["status"] == "degraded"
    assert result["items"] == [item]
    assert "host-inventory 数据已降级" in result["error"]


def test_source_error_is_sanitized(monkeypatch):
    monkeypatch.setattr(main, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(URLError("credential=secret")))
    with pytest.raises(HTTPException) as error:
        main.get("/alert-events")
    assert error.value.status_code == 502
    assert "secret" not in error.value.detail


def test_source_failure_keeps_persisted_snapshot_and_marks_degraded(monkeypatch, store):
    now = datetime.now(timezone.utc)
    store.save({"events": [{"id": "event-1", "status": "firing", "created_at": now.isoformat()}],
                "rules": [], "silences": []}, now)
    monkeypatch.setattr(main, "_refresh_seconds", 0)
    monkeypatch.setattr(main, "get", lambda *args, **kwargs: (_ for _ in ()).throw(HTTPException(502, "secret")))
    result = TestClient(app).get("/api/v1/summary").json()
    assert result["status"] == "degraded"
    assert result["data"]["firing"] == 1
    assert "secret" not in result["error"]


def test_alert_writes_are_module_owned_and_audited(store, monkeypatch):
    now = datetime.now(timezone.utc)
    store.save({"events": [{"id": "event-1", "rule_id": "rule-1", "status": "firing"}],
                "rules": [{"id": "rule-1", "name": "CPU"}], "silences": []}, now)
    monkeypatch.setenv("ALERTS_SYNC_ENABLED", "false")
    monkeypatch.setitem(app.dependency_overrides, app.state.require_editor,
                        lambda: {"username": "alice", "role": "editor"})
    client = TestClient(app)

    ack = client.post("/api/v1/events/event-1/ack")
    assert ack.status_code == 200
    assert store.value[0]["events"][0]["acked_by"] == "alice"

    created = client.post("/api/v1/rules", json={
        "name": "Memory", "metric": "memory", "threshold": "90",
    })
    assert created.status_code == 201
    assert len(store.value[0]["rules"]) == 2

    end = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    silence = client.post("/api/v1/silences", json={
        "starts_at": now.isoformat(), "ends_at": end, "reason": "maintenance",
    })
    assert silence.status_code == 201
    assert store.value[0]["silences"][0]["created_by"] == "alice"
    monkeypatch.delitem(app.dependency_overrides, app.state.require_editor)


def test_alert_evaluator_applies_duration_and_recovers(store, monkeypatch):
    now = datetime.now(timezone.utc)
    host_id = "host-1"
    store.save({"events": [], "silences": [], "rules": [{
        "id": "rule-1", "name": "CPU", "metric": "cpu", "value_type": "numeric",
        "operator": ">", "threshold": "50", "duration_sec": 0, "cooldown_sec": 0, "enabled": True,
    }]}, now)
    monkeypatch.setenv("ALERTS_SYNC_ENABLED", "false")
    monkeypatch.setattr(main, "_module_get", lambda url, token, path: (
        {"status": "ok", "collected_at": now.isoformat(), "items": [{"id": host_id, "name": "node-1", "status": "online", "agent_status": "running"}]}
        if path.endswith("/hosts") else
        {"items": [{"id": host_id, "last_sample_at": now.isoformat(), "metrics": {"cpu": 70}}]}
    ))

    assert main.evaluate_once() is True
    assert store.value[0]["events"][0]["status"] == "pending"
    assert main.evaluate_once() is True
    assert store.value[0]["events"][0]["status"] == "firing"

    monkeypatch.setattr(main, "_module_get", lambda url, token, path: (
        {"status": "ok", "collected_at": now.isoformat(), "items": [{"id": host_id, "name": "node-1", "status": "online", "agent_status": "running"}]}
        if path.endswith("/hosts") else
        {"items": [{"id": host_id, "last_sample_at": now.isoformat(), "metrics": {"cpu": 10}}]}
    ))
    assert main.evaluate_once() is True
    assert store.value[0]["events"][0]["status"] == "recovered"


def test_alert_evaluator_checks_certificates_without_host_inventory(store, monkeypatch):
    now = datetime.now(timezone.utc)
    store.certificates = [{"id": "cert-1", "domain": "example.com", "enabled": True,
                           "days_left": 10, "updated_at": now.isoformat()}]
    store.save({"events": [], "silences": [], "rules": [{
        "id": "cert-rule", "name": "证书即将过期", "metric": "cert_days_left", "value_type": "numeric",
        "operator": "<", "threshold": "30", "duration_sec": 0, "cooldown_sec": 0, "enabled": True,
    }]}, now)
    monkeypatch.setenv("ALERTS_SYNC_ENABLED", "false")
    monkeypatch.setattr(main, "_module_get", lambda *args: None)

    assert main.evaluate_once() is True
    event = store.value[0]["events"][0]
    assert event["server_id"] == "cert:cert-1"
    assert event["server_name"] == "example.com"
    assert event["current_value"] == "10"


def test_certificate_scan_rejects_private_addresses(monkeypatch):
    monkeypatch.setattr(main.socket, "getaddrinfo", lambda *args, **kwargs: [
        (main.socket.AF_INET, main.socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ])
    with pytest.raises(ValueError, match="公网地址"):
        main.check_certificate("localhost")


def test_certificate_scan_saves_results(store, monkeypatch):
    store.certificates = [{"id": "cert-1", "domain": "example.com", "port": 443, "enabled": True}]
    monkeypatch.setattr(main, "check_certificate", lambda domain, port: {
        "days_left": 42, "not_after": "2026-12-01T00:00:00+00:00", "issuer": "Example CA",
    })

    assert main.scan_certificates() == 1
    assert store.certificates[0]["days_left"] == 42
    assert store.certificates[0]["last_error"] is None
    assert store.certificates[0]["updated_at"]


def test_certificate_scan_does_not_resurrect_removed_target(store, monkeypatch):
    store.certificates = [{"id": "cert-1", "domain": "example.com", "port": 443, "enabled": True}]
    def scan(domain, port):
        store.certificates = []
        return {"days_left": 42, "not_after": "2026-12-01T00:00:00+00:00", "issuer": "Example CA"}
    monkeypatch.setattr(main, "check_certificate", scan)

    assert main.scan_certificates() == 1
    assert store.certificates == []


def test_certificate_targets_are_module_owned_and_authenticated(store, monkeypatch):
    monkeypatch.setenv("ALERTS_SYNC_ENABLED", "false")
    client = TestClient(app)
    assert client.post("/api/v1/certificates", json={"domain": "example.com"}).status_code == 401
    monkeypatch.setitem(app.dependency_overrides, app.state.require_editor,
                        lambda: {"username": "alice", "role": "editor"})
    response = client.post("/api/v1/certificates", json={"domain": "EXAMPLE.com."})
    assert response.status_code == 201
    assert store.certificates[0]["domain"] == "example.com"
    assert client.post("/api/v1/certificates", json={"domain": "example.com"}).status_code == 409
    assert client.get("/api/v1/certificates").json()[0]["domain"] == "example.com"
    assert client.post("/api/v1/certificates", json={"domain": "bad..example"}).status_code == 422
    monkeypatch.delitem(app.dependency_overrides, app.state.require_editor)
