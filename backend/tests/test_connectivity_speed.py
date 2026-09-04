"""Local terminal and asynchronous Agent upgrade regression tests."""
import uuid

from fastapi.testclient import TestClient
import pytest

from app import main
from app.main import Base, SessionLocal, app, engine
from app.models import Server


client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def add_server(*, local=False, version="2.3.0", credentials=True):
    server_id = uuid.uuid4()
    with SessionLocal() as db:
        db.add(Server(
            id=server_id, name="VM2" if local else "node-old",
            host="10.66.66.5" if local else "10.66.66.8",
            ssh_port=22, ssh_user="prod", agent_type="local" if local else "remote",
            is_local=local, ssh_key="__password__secret" if credentials else None,
            agent_status="running", agent_version=version,
        ))
        db.commit()
    return str(server_id)


def test_local_terminal_does_not_require_ssh_credentials(monkeypatch):
    server_id = add_server(local=True, credentials=False)
    captured = {}

    class Session:
        def connect(self, cols=80, rows=24):
            return True

    def fake_create_session(**kwargs):
        captured.update(kwargs)
        return "local-session", ""

    monkeypatch.setattr(main, "create_session", fake_create_session)
    monkeypatch.setattr(main, "get_session", lambda _sid: Session())
    response = client.post("/api/v2/terminal/sessions", json={"server_id": server_id, "cols": 100, "rows": 30})
    assert response.status_code == 200, response.text
    assert response.json()["transport"] == "local-pty"
    assert captured["local"] is True
    assert captured["password"] is None and captured["key_content"] is None


def test_outdated_agent_detection_and_async_upgrade(monkeypatch):
    server_id = add_server(version="2.3.0")
    current = client.get("/api/v2/agents/version")
    assert current.status_code == 200
    assert current.json()["current_version"] == "2.6.1"
    assert server_id in current.json()["outdated_server_ids"]

    upgraded = []
    monkeypatch.setattr(main, "_deploy_agent_background", lambda value, password=None: upgraded.append(value))
    response = client.post(f"/api/v2/servers/{server_id}/upgrade-agent")
    assert response.status_code == 202, response.text
    assert response.json()["target_version"] == "2.6.1"
    assert upgraded == [server_id]


def test_startup_upgrade_only_targets_stale_agents_with_credentials(monkeypatch):
    old_id = add_server(version="2.2.0")
    add_server(version="2.6.1")
    add_server(version="2.1.0", credentials=False)
    upgraded = []
    monkeypatch.setattr(main, "_deploy_agent_background", lambda value, password=None: upgraded.append(value))
    targets = main._upgrade_outdated_agents_once()
    assert targets == [old_id]
    assert upgraded == [old_id]
