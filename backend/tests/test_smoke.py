"""OpsCenter Smoke Tests — baseline before refactoring

Uses the same PostgreSQL server as production, but a separate test database (opscenter_test).
"""

import pytest
import os
import sys

# CI（GitLab Runner）通过环境变量注入 DATABASE_URL；本地未设置时用默认值
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://opscenter:opscenter123@127.0.0.1:5433/opscenter_test")
os.environ["OPS_AUTH_ENABLED"] = "false"
os.environ["LOCAL_HOST"] = "127.0.0.1"
sys.path.insert(0, "/opt/opscenter/backend")

from app.main import app, Base, engine
from fastapi.testclient import TestClient

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup():
    """Recreate all tables before each test (clean state)."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def test_health():
    r = client.get("/api/v2/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_list_servers():
    r = client.get("/api/v2/servers")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_add_server():
    r = client.post("/api/v2/servers", json={
        "name": "SmokeTest", "host": "10.0.0.1", "ssh_key": "__password__x"
    })
    assert r.status_code in (200, 201)  # 201 Created is also valid
    data = r.json()
    assert "id" in data
    assert data["name"] == "SmokeTest"


def test_stats():
    r = client.get("/api/v2/stats")
    assert r.status_code == 200


def test_auth_module():
    from app.auth import (
        hash_password, verify_password, create_access_token, decode_token
    )
    h = hash_password("test")
    assert verify_password("test", h)
    assert not verify_password("wrong", h)
    t = create_access_token(1, "admin")
    payload = decode_token(t)
    assert payload is not None
    assert payload["sub"] == "1"


def test_terminal_stats():
    r = client.get("/api/v2/terminal/stats")
    assert r.status_code == 200
    assert "active_sessions" in r.json()


def test_terminal_requires_ssh():
    """Terminal session creation without SSH credentials returns 400."""
    r = client.post("/api/v2/servers", json={
        "name": "NoSSH", "host": "10.0.0.99", "ssh_key": ""
    })
    sid = r.json()["id"]
    r2 = client.post("/api/v2/terminal/sessions", json={
        "server_id": sid, "cols": 80, "rows": 24
    })
    assert r2.status_code == 400
