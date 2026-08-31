"""Editable service-plaza profile and credential security contracts."""
import socket
import uuid
from urllib.error import URLError

import pytest
from fastapi.testclient import TestClient

from app.credential_crypto import decrypt_secret
from app.main import Base, SessionLocal, app, engine
from app.models import (
    PlazaCredentialAccess, PlazaHealthIncident, PlazaHealthState,
    PlazaProbeResult, PlazaServiceProfile,
)
from app import plaza


client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_database():
    with plaza._cycle_lock:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        with plaza._cache_lock:
            plaza._cached_checks.clear()
            plaza._cached_at = 0.0
            plaza._refreshing = False
        plaza._probe_times.clear()
    yield


def add_host(host="10.66.66.4"):
    response = client.post("/api/v2/servers", json={
        "name": "测试主机", "host": host, "ssh_port": 22, "auto_deploy_agent": False,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_catalog_profile_can_be_edited_and_password_is_encrypted():
    server_id = add_host()
    response = client.put("/api/v2/services/plaza/gitea", json={
        "server_id": server_id, "name": "内部 Gitea", "description": "研发代码托管",
        "category": "代码与CI/CD", "entry_url": "http://10.66.66.4:3000/",
        "health_url": "http://10.66.66.4:3000/api/healthz", "username": "dev-admin",
        "password": "plaza-secret", "login_notes": "使用本地账号登录",
        "owner": "研发平台组", "tags": ["代码", "核心"],
    })
    assert response.status_code == 200, response.text
    assert "plaza-secret" not in response.text

    with SessionLocal() as db:
        profile = db.query(PlazaServiceProfile).filter(PlazaServiceProfile.plaza_key == "gitea").first()
        assert profile.secret_ciphertext and "plaza-secret" not in profile.secret_ciphertext
        assert decrypt_secret(profile.secret_ciphertext) == "plaza-secret"

    detail_response = client.get("/api/v2/services/plaza/gitea/detail")
    detail = detail_response.json()
    assert detail_response.status_code == 200
    assert detail["name"] == "内部 Gitea"
    assert detail["server"]["id"] == server_id
    assert detail["credential_username"] == "dev-admin"
    assert detail["has_credentials"] is True
    assert detail["owner"] == "研发平台组"
    assert detail["tags"] == ["代码", "核心"]
    assert "password" not in detail and "secret_ciphertext" not in detail

    reveal = client.post("/api/v2/services/plaza/gitea/credentials/reveal")
    assert reveal.status_code == 200
    assert reveal.headers["cache-control"].startswith("no-store")
    assert reveal.json() == {"username": "dev-admin", "password": "plaza-secret"}
    with SessionLocal() as db:
        access = db.query(PlazaCredentialAccess).filter(
            PlazaCredentialAccess.plaza_key == "gitea",
        ).one()
        assert access.actor == "admin"
        assert "plaza-secret" not in repr(access.__dict__)
    history = client.get("/api/v2/services/plaza/gitea/credential-access-history").json()
    assert len(history) == 1 and history[0]["actor"] == "admin"
    assert "password" not in history[0]


def test_blank_password_preserves_existing_and_explicit_clear_removes_it():
    add_host()
    assert client.put("/api/v2/services/plaza/gitea", json={"password": "keep-me"}).status_code == 200
    assert client.put("/api/v2/services/plaza/gitea", json={"description": "changed", "password": ""}).status_code == 200
    assert client.post("/api/v2/services/plaza/gitea/credentials/reveal").json()["password"] == "keep-me"
    assert client.put("/api/v2/services/plaza/gitea", json={"clear_password": True}).status_code == 200
    assert client.post("/api/v2/services/plaza/gitea/credentials/reveal").status_code == 404


def test_manual_service_creation_stores_credentials_in_profile_only():
    server_id = add_host("10.66.66.20")
    response = client.post(f"/api/v2/services?server_id={server_id}", json={
        "name": "内部 Wiki", "url": "http://10.66.66.20:8080/", "category": "应用服务",
        "account": "wiki-admin", "password": "wiki-secret",
    })
    assert response.status_code == 201, response.text
    service_id = response.json()["id"]
    with SessionLocal() as db:
        profile = db.query(PlazaServiceProfile).filter(
            PlazaServiceProfile.plaza_key == f"manual-{uuid.UUID(service_id)}",
        ).first()
        assert profile.username == "wiki-admin"
        assert decrypt_secret(profile.secret_ciphertext) == "wiki-secret"

    rows = client.get("/api/v2/services/plaza").json()
    row = next(item for item in rows if item["service_id"] == service_id)
    assert row["has_credentials"] is True
    assert "password" not in row and "credential_username" not in row


def test_probe_policy_manual_probe_and_history(monkeypatch):
    update = client.put("/api/v2/services/plaza/gitea", json={
        "probe_enabled": False,
        "probe_interval_seconds": 300,
        "probe_timeout_seconds": 2.5,
        "probe_success_statuses": "200-299,401",
        "probe_verify_tls": False,
    })
    assert update.status_code == 200, update.text
    detail = client.get("/api/v2/services/plaza/gitea/detail").json()
    assert detail["probe_policy"] == {
        "enabled": False, "interval_seconds": 300, "timeout_seconds": 2.5,
        "success_statuses": "200-299,401", "verify_tls": False,
        "failure_threshold": 3, "recovery_threshold": 1,
        "notifications_enabled": True,
    }

    monkeypatch.setattr(plaza, "_probe", lambda _item: {
        "status": "up", "http_status": 204, "latency_ms": 12,
        "health_error": "", "checked_at": "2026-08-31T00:00:00",
    })
    result = client.post("/api/v2/services/plaza/gitea/probe")
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "up"
    with SessionLocal() as db:
        row = db.query(PlazaProbeResult).filter(PlazaProbeResult.plaza_key == "gitea").one()
        assert row.http_status == 204 and row.latency_ms == 12

    history = client.get("/api/v2/services/plaza/gitea/probe-history?hours=24").json()
    assert len(history) == 1 and history[0]["status"] == "up"
    detail = client.get("/api/v2/services/plaza/gitea/detail").json()
    assert detail["probe_summary"]["checks_24h"] == 1
    assert detail["probe_summary"]["uptime_percent_24h"] == 100.0


def test_probe_success_status_validation():
    response = client.put("/api/v2/services/plaza/gitea", json={
        "probe_success_statuses": "200-999",
    })
    assert response.status_code == 422


@pytest.mark.parametrize(("exc", "expected_code", "expected_message"), [
    (URLError(ConnectionRefusedError(10061, "refused")), "connection_refused", "目标拒绝连接"),
    (URLError(socket.timeout("timed out")), "timeout", "连接超时"),
    (URLError(socket.gaierror(11001, "host not found")), "dns_error", "域名解析失败"),
])
def test_probe_network_errors_are_classified(exc, expected_code, expected_message):
    assert plaza._classify_probe_exception(exc) == (expected_code, expected_message)


def test_health_overview_aggregates_history_without_probing(monkeypatch):
    monkeypatch.setattr(plaza, "_probe", lambda _item: pytest.fail("overview must not probe"))
    with SessionLocal() as db:
        db.add_all([
            PlazaProbeResult(plaza_key="gitea", status="up", http_status=200, latency_ms=10),
            PlazaProbeResult(plaza_key="gitea", status="down", http_status=500, latency_ms=30),
        ])
        db.commit()
    response = client.get("/api/v2/services/plaza/health-overview?hours=24")
    assert response.status_code == 200, response.text
    data = response.json()
    gitea = next(item for item in data["items"] if item["key"] == "gitea")
    assert gitea["checks"] == 2
    assert gitea["uptime_percent"] == 50.0
    assert gitea["avg_latency_ms"] == 20.0
    assert data["summary"]["average_uptime_percent"] == 50.0


def test_persistent_threshold_incident_acknowledge_and_recovery(monkeypatch):
    add_host()
    assert client.put("/api/v2/services/plaza/gitea", json={
        "probe_failure_threshold": 2, "probe_recovery_threshold": 1,
    }).status_code == 200
    sent = []
    monkeypatch.setattr(plaza, "_send_incident_notification", lambda kind, _item, incident: sent.append((kind, incident.id)) or True)
    monkeypatch.setattr(plaza, "_probe", lambda _item: {
        "status": "down", "http_status": 503, "latency_ms": 8,
        "health_error_code": "http_status", "health_error": "HTTP 503 状态不符合成功策略",
        "checked_at": "2026-08-31T00:00:00",
    })
    assert client.post("/api/v2/services/plaza/gitea/probe").status_code == 200
    with SessionLocal() as db:
        state = db.query(PlazaHealthState).filter(PlazaHealthState.plaza_key == "gitea").one()
        assert state.stable_status == "degraded" and state.active_incident_id is None

    assert client.post("/api/v2/services/plaza/gitea/probe").status_code == 200
    incidents = client.get("/api/v2/services/plaza/incidents?status=open").json()
    assert incidents["total"] == 1 and len(sent) == 1 and sent[0][0] == "alert"
    assert incidents["items"][0]["service_name"] == "Gitea"
    assert incidents["items"][0]["server_name"] == "测试主机"
    assert incidents["items"][0]["server_host"] == "10.66.66.4"
    assert incidents["items"][0]["entry_url"].startswith("http://10.66.66.4:3000")
    assert incidents["items"][0]["last_error_code"] == "http_status"
    incident_id = incidents["items"][0]["id"]
    acknowledged = client.post(f"/api/v2/services/plaza/incidents/{incident_id}/acknowledge")
    assert acknowledged.status_code == 200 and acknowledged.json()["status"] == "acknowledged"

    monkeypatch.setattr(plaza, "_probe", lambda _item: {
        "status": "up", "http_status": 200, "latency_ms": 5,
        "health_error": "", "checked_at": "2026-08-31T00:01:00",
    })
    assert client.post("/api/v2/services/plaza/gitea/probe").status_code == 200
    resolved = client.get(f"/api/v2/services/plaza/incidents/{incident_id}").json()
    assert resolved["status"] == "resolved"
    assert [kind for kind, _ in sent] == ["alert", "recovery"]
    with SessionLocal() as db:
        assert db.query(PlazaHealthIncident).count() == 1


def test_active_silence_suppresses_notification_but_keeps_incident(monkeypatch):
    assert client.put("/api/v2/services/plaza/gitea", json={"probe_failure_threshold": 1}).status_code == 200
    silence = client.post("/api/v2/services/plaza/silences", json={
        "plaza_key": "gitea", "ends_at": "2026-09-01T00:00:00", "reason": "计划维护",
    })
    assert silence.status_code == 201, silence.text
    monkeypatch.setattr(plaza, "_send_incident_notification", lambda *_args: pytest.fail("silence must suppress notification"))
    monkeypatch.setattr(plaza, "_probe", lambda _item: {
        "status": "down", "http_status": 503, "latency_ms": 8,
        "health_error": "HTTP 503", "checked_at": "2026-08-31T00:00:00",
    })
    assert client.post("/api/v2/services/plaza/gitea/probe").status_code == 200
    assert client.get("/api/v2/services/plaza/incidents?status=open").json()["total"] == 1
    assert client.get("/api/v2/services/plaza/silences?active=true").json()[0]["active"] is True
    ended = client.delete(f"/api/v2/services/plaza/silences/{silence.json()['id']}")
    assert ended.status_code == 200 and ended.json()["active"] is False
