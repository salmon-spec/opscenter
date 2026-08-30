"""Alloy log collector lifecycle API tests."""

import os
import sys
import time

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://opscenter:opscenter123@127.0.0.1:5433/opscenter_test",
)
os.environ["OPS_AUTH_ENABLED"] = "false"
os.environ["LOCAL_HOST"] = "127.0.0.1"

sys.path.insert(0, "/opt/opscenter/backend")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import alloy_manager  # noqa: E402
from app.main import Base, SessionLocal, app, engine  # noqa: E402
from app.models import Server  # noqa: E402


client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_database(monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(alloy_manager, "LOKI_URL", "http://10.66.66.5:3100")
    monkeypatch.setattr(alloy_manager, "_run", lambda *args: None)
    yield
    Base.metadata.drop_all(bind=engine)


def _server(*, local=False, credentials=True, log_status="unknown"):
    db = SessionLocal()
    try:
        row = Server(
            name="alloy-host", host="10.66.66.81", ssh_user="root",
            ssh_key="__password__x" if credentials else None,
            agent_type="local" if local else "remote", is_local=local,
            log_agent_status=log_status,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return str(row.id)
    finally:
        db.close()


def test_deploy_and_check_are_accepted_without_blocking():
    server_id = _server()
    deploy = client.post(f"/api/v2/servers/{server_id}/logs/agent/deploy")
    assert deploy.status_code == 202
    assert deploy.json()["target_version"] == "1.18.0"
    db = SessionLocal()
    try:
        assert db.query(Server).first().log_agent_status == "deploying"
    finally:
        db.close()

    check = client.post(f"/api/v2/servers/{server_id}/logs/agent/check")
    assert check.status_code == 202
    db = SessionLocal()
    try:
        assert db.query(Server).first().log_agent_status == "checking"
    finally:
        db.close()


def test_deploy_requires_loki_credentials_and_remote_host(monkeypatch):
    remote = _server(credentials=False)
    assert client.post(f"/api/v2/servers/{remote}/logs/agent/deploy").status_code == 400

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    local = _server(local=True)
    assert client.post(f"/api/v2/servers/{local}/logs/agent/deploy").status_code == 400

    monkeypatch.setattr(alloy_manager, "LOKI_URL", "")
    assert client.get("/api/v2/logs/agents/version").json()["loki_configured"] is False
    assert client.post(f"/api/v2/servers/{local}/logs/agent/deploy").status_code == 503


def test_batch_only_schedules_missing_credentialed_remote_hosts():
    missing = _server()
    db = SessionLocal()
    try:
        db.add(Server(name="ready", host="10.66.66.82", ssh_key="__password__x", agent_type="remote", log_agent_status="running"))
        db.add(Server(name="no-creds", host="10.66.66.83", agent_type="remote", log_agent_status="unknown"))
        db.add(Server(name="local", host="127.0.0.1", agent_type="local", is_local=True, log_agent_status="unknown"))
        db.commit()
    finally:
        db.close()
    response = client.post("/api/v2/logs/agents/deploy-missing")
    assert response.status_code == 202
    assert response.json()["server_ids"] == [missing]


def test_version_parser_and_systemd_escaping():
    assert alloy_manager._version("alloy, version v1.18.0 (branch: HEAD)") == "1.18.0"
    escaped = alloy_manager._systemd_escape('node%1 "db"\nnext')
    assert escaped == 'node%%1 \\"db\\" next'


def test_overview_is_fast_by_default_and_reports_coverage(monkeypatch):
    running = _server(log_status="running")
    _server(credentials=False, log_status="unknown")
    monkeypatch.setattr(alloy_manager.requests, "get", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected Loki probe")))

    response = client.get("/api/v2/logs/agents/overview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["probed"] is False
    assert payload["total"] == 2
    assert payload["running"] == 1
    assert payload["coverage_percent"] == 50.0
    agents = {item["server_id"]: item for item in payload["agents"]}
    assert agents[running]["ingestion_status"] == "not_probed"
    assert any(item["diagnostic"] == "缺少 SSH 凭证，无法自动部署" for item in payload["agents"])


def test_explicit_overview_probe_detects_fresh_and_missing_logs(monkeypatch):
    fresh_id = _server(log_status="running")
    missing_id = _server(log_status="running")
    calls = []

    class ProbeResponse:
        def __init__(self, params):
            self.params = params

        def raise_for_status(self):
            return None

        def json(self):
            values = [[str(time.time_ns()), "fresh"]] if fresh_id in self.params["query"] else []
            result = [{"stream": {}, "values": values}] if values else []
            return {"status": "success", "data": {"result": result}}

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        return ProbeResponse(params)

    monkeypatch.setattr(alloy_manager.requests, "get", fake_get)
    response = client.get("/api/v2/logs/agents/overview", params={"probe": "true"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["probed"] is True
    assert payload["fresh"] == 1
    agents = {item["server_id"]: item for item in payload["agents"]}
    assert agents[fresh_id]["ingestion_status"] == "fresh"
    assert agents[fresh_id]["diagnostic"] == "采集正常"
    assert agents[missing_id]["ingestion_status"] == "no_data"
    assert agents[missing_id]["diagnostic_level"] == "warning"
    assert len(calls) == 2
    assert all(call["limit"] == 1 and call["direction"] == "backward" for call in calls)
