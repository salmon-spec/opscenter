"""Host-scoped Loki gateway tests."""

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

from app import log_center  # noqa: E402
from app.main import Base, SessionLocal, app, engine  # noqa: E402
from app.models import Server  # noqa: E402


client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_database(monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(log_center, "LOKI_URL", "http://loki.test:3100")
    yield
    Base.metadata.drop_all(bind=engine)


def _server_id():
    db = SessionLocal()
    try:
        server = Server(name="log-host", host="10.66.66.80", ssh_key="__password__x")
        db.add(server)
        db.commit()
        db.refresh(server)
        return str(server.id)
    finally:
        db.close()


class _Response:
    ok = True
    text = "ready"

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "status": "success",
            "data": {"result": [{
                "stream": {"server_id": "hidden", "source": "journal", "service_name": "sshd.service", "secret": "drop"},
                "values": [["1788076800000000000", "accepted connection"]],
            }]},
        }


def test_query_is_scoped_to_host_and_sanitizes_labels(monkeypatch):
    server_id = _server_id()
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured.update({"url": url, "params": params, "timeout": timeout})
        return _Response()

    monkeypatch.setattr(log_center.requests, "get", fake_get)
    response = client.get(
        f"/api/v2/servers/{server_id}/logs/query",
        params={
            "start": "2026-08-30T00:00:00Z", "end": "2026-08-30T01:00:00Z",
            "source": "journal", "service": "sshd.service", "search": "accepted",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert captured["params"]["query"] == f'{{server_id="{server_id}",source="journal",service_name="sshd.service"}} |= "accepted"'
    assert payload["count"] == 1
    assert payload["entries"][0]["labels"] == {"source": "journal", "service_name": "sshd.service"}


def test_query_rejects_unbounded_or_injected_input(monkeypatch):
    server_id = _server_id()
    injected = client.get(
        f"/api/v2/servers/{server_id}/logs/query",
        params={"service": 'sshd"} or {source="docker'},
    )
    assert injected.status_code == 400

    too_long = client.get(
        f"/api/v2/servers/{server_id}/logs/query",
        params={"start": "2024-01-01T00:00:00Z", "end": "2026-08-30T00:00:00Z"},
    )
    assert too_long.status_code == 400


def test_status_handles_unconfigured_loki(monkeypatch):
    monkeypatch.setattr(log_center, "LOKI_URL", "")
    response = client.get("/api/v2/logs/status")
    assert response.status_code == 200
    assert response.json()["configured"] is False
