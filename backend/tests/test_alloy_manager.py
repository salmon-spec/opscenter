"""Alloy log collector lifecycle API tests."""

import os
import sys

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
