"""v4.8 P0: host deletion - fast inventory removal without implicit agent uninstall."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import Base, SessionLocal, app, engine
from app.models import Server, Service

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def add_host(name="node-a", host="10.66.66.20", **extra):
    payload = {"name": name, "host": host, "ssh_port": 22, "auto_deploy_agent": False, **extra}
    response = client.post("/api/v2/servers", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_delete_remote_host_is_fast_inventory_remove_without_agent_uninstall(tmp_path, monkeypatch):
    # uninstall_agent 必须完全不被调用（即使主机 agent_status=running）
    from app import agent_manager

    called = []
    monkeypatch.setattr(agent_manager, "uninstall_agent", lambda *a, **k: called.append(a))
    groups = tmp_path / "groups.json"
    groups.write_text(json.dumps({"servers": {}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("GROUPS_JSON_PATH", str(groups))

    server_id = add_host(name="node-b", host="10.66.66.21", ssh_password="keep-me")
    with SessionLocal() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        srv.agent_status = "running"  # 模拟旧逻辑会触发远程卸载的状态
        db.add(Service(server_id=srv.id, name="svc-a", url="tcp://10.66.66.21:8080", port=8080, proto="tcp"))
        db.commit()

    resp = client.delete(f"/api/v2/servers/{server_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["agent_uninstalled"] is False
    assert data["deleted"] == {"servers": 1, "services": 1, "databases": 0}
    assert data["warnings"] == []
    assert called == []  # 卸载函数不得执行

    with SessionLocal() as db:
        assert db.query(Server).filter(Server.id == uuid.UUID(server_id)).first() is None
        assert db.query(Service).filter(Service.name == "svc-a").count() == 0


def test_delete_still_commits_when_groups_json_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("GROUPS_JSON_PATH", str(tmp_path / "none.json"))
    server_id = add_host()
    resp = client.delete(f"/api/v2/servers/{server_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["agent_uninstalled"] is False
    assert len(data["warnings"]) == 1  # 数据库删除已提交，仅非关键清理失败
    with SessionLocal() as db:
        assert db.query(Server).filter(Server.id == uuid.UUID(server_id)).first() is None


def test_delete_local_host_rejected():
    server_id = add_host(name="local", host="127.0.0.1", is_local=True)
    assert client.delete(f"/api/v2/servers/{server_id}").status_code == 400


def test_delete_missing_and_invalid_id_are_clear_errors():
    assert client.delete(f"/api/v2/servers/{uuid.uuid4()}").status_code == 404
    assert client.delete("/api/v2/servers/not-a-uuid").status_code == 422