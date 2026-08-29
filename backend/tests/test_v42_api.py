"""OpsCenter 4.2 API contract checks backed by the configured test database."""
import os
import uuid

os.environ.setdefault("OPS_AUTH_ENABLED", "false")

import pytest
from fastapi.testclient import TestClient

from app import control, system_control
from app.main import Base, SessionLocal, app, engine
from app.models import DatabaseInstance, Server

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    control._CONTAINER_CACHE.clear()
    yield


def add_host(name="node-a", host="10.66.66.20", **extra):
    payload = {"name": name, "host": host, "ssh_port": 22, "auto_deploy_agent": False, **extra}
    response = client.post("/api/v2/servers", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_host_duplicate_partial_update_and_credential_replacement():
    server_id = add_host(ssh_password="old-password")
    duplicate = client.post("/api/v2/servers", json={"name": "same target", "host": "10.66.66.20", "ssh_port": 22, "auto_deploy_agent": False})
    assert duplicate.status_code == 409

    assert client.put(f"/api/v2/servers/{server_id}", json={"name": "renamed"}).status_code == 200
    with SessionLocal() as db:
        row = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        assert row.name == "renamed"
        assert row.ssh_key == "__password__old-password"

    assert client.put(f"/api/v2/servers/{server_id}", json={"ssh_password": "new-password"}).status_code == 200
    with SessionLocal() as db:
        assert db.query(Server).filter(Server.id == uuid.UUID(server_id)).first().ssh_key == "__password__new-password"


def test_local_host_address_and_delete_are_protected():
    server_id = add_host(name="local", host="127.0.0.1", is_local=True)
    assert client.put(f"/api/v2/servers/{server_id}", json={"name": "local renamed"}).status_code == 200
    assert client.put(f"/api/v2/servers/{server_id}", json={"host": "10.66.66.99"}).status_code == 400
    assert client.delete(f"/api/v2/servers/{server_id}").status_code == 400


def test_database_instance_never_returns_plaintext_and_requires_delete_confirmation():
    server_id = add_host()
    response = client.post("/api/v2/databases/instances", json={
        "server_id": server_id, "name": "mysql-main", "engine": "mysql",
        "host": "127.0.0.1", "port": 3306, "username": "ops", "password": "db-secret",
        "connection_mode": "ssh",
    })
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["has_credentials"] is True
    assert "db-secret" not in response.text
    with SessionLocal() as db:
        encrypted = db.query(DatabaseInstance).filter(DatabaseInstance.id == uuid.UUID(data["id"])).first().secret_ciphertext
        assert encrypted and "db-secret" not in encrypted

    assert client.delete(f"/api/v2/databases/instances/{data['id']}").status_code == 422
    assert client.delete(f"/api/v2/databases/instances/{data['id']}?confirm_name=wrong").status_code == 400
    assert client.delete(f"/api/v2/databases/instances/{data['id']}?confirm_name=mysql-main").status_code == 200


def test_container_basic_api_does_not_request_stats(monkeypatch):
    server_id = add_host()
    requested = []
    monkeypatch.setattr(control, "_load_container_rows", lambda _server, include_stats=True: requested.append(include_stats) or [])
    response = client.get(f"/api/v2/servers/{server_id}/containers?include_stats=false&refresh=true")
    assert response.status_code == 200
    assert requested == [False]
    assert response.json()["stats_timestamp"] is None


def test_system_summary_contract_and_illegal_pid(monkeypatch):
    server_id = add_host()
    with SessionLocal() as db:
        row = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        row.agent_status = "running"
        row.agent_token = "test-token"
        db.commit()
    monkeypatch.setattr(system_control, "fetch_agent_system_summary", lambda *_args, **_kwargs: {
        "timestamp": 1000, "agent_version": "2.4.0", "hostname": "node-a",
        "cpu_percent": 12.5, "memory_percent": 31.0, "disk_percent": 44.0,
    })
    response = client.get(f"/api/v2/servers/{server_id}/system/summary?refresh=true")
    assert response.status_code == 200
    assert response.json()["metrics"]["cpu"] == 12.5
    assert client.post(f"/api/v2/servers/{server_id}/processes/2/signal", json={"signal": "KILL"}).status_code == 400
