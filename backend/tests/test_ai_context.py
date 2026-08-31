"""Contracts for the strictly authenticated, read-only AI context API."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import plaza
from app import database
from app.api_keys import hash_api_key
from app.main import Base, SessionLocal, app, engine
from app.models import (
    AlertEvent,
    AlertRule,
    ApiKey,
    MetricHistory,
    PlazaHealthIncident,
    PlazaHealthState,
    Server,
    Service,
)


client = TestClient(app)
PLAIN_KEY = "oc_rt_ai-context-test"
AUTH = {"Authorization": f"Bearer {PLAIN_KEY}"}


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with plaza._cache_lock:
        plaza._cached_checks.clear()
        plaza._cached_at = 0.0
        plaza._refreshing = False
    with SessionLocal() as db:
        db.add(ApiKey(
            name="AI context test",
            key_hash=hash_api_key(PLAIN_KEY),
            prefix=PLAIN_KEY[:10],
            scope="read",
            enabled=True,
        ))
        db.commit()
    yield


def _seed_context():
    now = datetime.utcnow()
    with SessionLocal() as db:
        server = Server(
            name="VM-Test",
            host="10.66.66.20",
            status="online",
            agent_type="remote",
            agent_status="running",
            agent_version="2.4.0",
            agent_token="must-never-leak",
            ssh_key="__password__must-never-leak",
            last_seen=now,
            log_agent_status="running",
            log_agent_version="1.18.0",
            log_agent_checked_at=now,
        )
        db.add(server)
        db.flush()
        service = Service(
            server_id=server.id,
            name="AI Visible App",
            url="http://ops:url-secret-must-never-leak@10.66.66.20:8080/?token=query-secret-must-never-leak",
            health_path="http://10.66.66.20:8080/health?api_key=health-secret-must-never-leak",
            source="manual",
            status="up",
            password="legacy-secret-must-never-leak",
        )
        db.add(service)
        for metric, value in (("cpu", 12.5), ("memory", 42.0), ("disk", 55.5)):
            db.add(MetricHistory(server_id=server.id, metric=metric, value=value, timestamp=now))
        state = PlazaHealthState(
            plaza_key="gitea",
            stable_status="down",
            consecutive_failures=3,
            last_checked_at=now,
            last_error_code="connection_refused",
            last_error="目标拒绝连接",
        )
        incident = PlazaHealthIncident(
            plaza_key="gitea",
            status="open",
            opened_at=now,
            last_error_code="connection_refused",
            last_error="目标拒绝连接",
            failure_count_at_open=3,
        )
        db.add_all([state, incident])
        db.flush()
        state.active_incident_id = incident.id
        rule = AlertRule(name="CPU 过高", metric="cpu", threshold="90")
        db.add(rule)
        db.flush()
        db.add(AlertEvent(
            rule_id=rule.id,
            server_id=server.id,
            status="firing",
            current_value="95",
            fired_at=now - timedelta(minutes=2),
        ))
        db.commit()


def test_ai_context_requires_a_valid_key_even_when_ui_auth_is_disabled():
    assert engine is database.engine
    assert SessionLocal is database.SessionLocal
    assert client.get("/api/v2/ai/summary").status_code == 401
    assert client.get(
        "/api/v2/ai/summary",
        headers={"Authorization": "Bearer invalid"},
    ).status_code == 401
    capabilities = client.get("/api/v2/ai/capabilities", headers=AUTH)
    assert capabilities.status_code == 200
    assert len(capabilities.json()["data"]["endpoints"]) == 6
    paths = client.app.openapi()["paths"]
    assert {path for path in paths if path.startswith("/api/v2/ai/")} == {
        "/api/v2/ai/capabilities", "/api/v2/ai/hosts", "/api/v2/ai/services",
        "/api/v2/ai/incidents", "/api/v2/ai/summary", "/api/v2/ai/snapshot",
    }


def test_ai_context_exposes_bounded_fresh_context_without_secrets(monkeypatch):
    _seed_context()
    monkeypatch.setattr(plaza, "_probe", lambda *_args: pytest.fail("AI reads must never probe"))

    summary = client.get("/api/v2/ai/summary", headers=AUTH)
    hosts = client.get("/api/v2/ai/hosts", headers=AUTH)
    services = client.get("/api/v2/ai/services", headers=AUTH)
    incidents = client.get("/api/v2/ai/incidents?active_only=true&limit=10", headers=AUTH)
    snapshot = client.get("/api/v2/ai/snapshot?incident_limit=10", headers=AUTH)

    for response in (summary, hosts, services, incidents, snapshot):
        assert response.status_code == 200, response.text
        assert response.json()["schema_version"] == "1.0"
        assert response.json()["opscenter_version"]
        assert "generated_at" in response.json()
        lowered = response.text.lower()
        assert "must-never-leak" not in lowered
        assert "agent_token" not in lowered
        assert "ssh_key" not in lowered
        assert "password" not in lowered
        assert "key_hash" not in lowered

    assert summary.json()["data"]["posture"] == "critical"
    assert summary.json()["data"]["hosts"]["total"] == 1
    assert hosts.json()["data"][0]["metrics"]["cpu"]["value"] == 12.5
    assert hosts.json()["data"][0]["metrics"]["cpu"]["stale"] is False
    assert any(row["key"] == "gitea" and row["status"] == "down" for row in services.json()["data"])
    manual = next(row for row in services.json()["data"] if row["name"] == "AI Visible App")
    assert manual["entry_url"] == "http://10.66.66.20:8080/"
    assert manual["health_url"] == "http://10.66.66.20:8080/health"
    assert {row["kind"] for row in incidents.json()["data"]} == {"service_health", "metric_alert"}
    assert len(snapshot.json()["data"]["hosts"]) == 1


def test_ai_context_query_bounds_are_enforced():
    assert client.get("/api/v2/ai/incidents?limit=501", headers=AUTH).status_code == 422
    assert client.get("/api/v2/ai/incidents?hours=0", headers=AUTH).status_code == 422
    assert client.get("/api/v2/ai/snapshot?incident_limit=201", headers=AUTH).status_code == 422
