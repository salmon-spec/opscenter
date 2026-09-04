"""v4.8: manual topology editing - layout save/load + relation create/delete (service scenarios only)."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import Base, SessionLocal, app, engine
from app.models import Server, Service, ServiceRelation

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def add_host(name="node-a", host="10.66.66.20"):
    r = client.post("/api/v2/servers", json={"name": name, "host": host, "ssh_port": 22, "auto_deploy_agent": False})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def add_service(server_id, name):
    with SessionLocal() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        svc = Service(server_id=srv.id, name=name, url=f"http://x/{name}", source="manual")
        db.add(svc)
        db.commit()
        return str(svc.id)


def test_layout_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("TOPOLOGY_LAYOUT_PATH", str(tmp_path / "layout.json"))
    pos = {"a": {"x": 12, "y": 34}, "b": {"x": 56, "y": 78}}
    r = client.post("/api/v2/topology/layout", json={"scenario": "cicd", "positions": pos})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    r = client.get("/api/v2/topology/layout?scenario=cicd")
    assert r.status_code == 200
    data = r.json()
    assert data["positions"] == pos
    assert data["saved_at"]


def test_layout_rejects_wireguard_scenario(tmp_path, monkeypatch):
    monkeypatch.setenv("TOPOLOGY_LAYOUT_PATH", str(tmp_path / "layout.json"))
    r = client.post("/api/v2/topology/layout", json={"scenario": "wireguard", "positions": {}})
    assert r.status_code == 422  # WireGuard 只读，不接受手动布局


def test_relation_create_upsert_and_list(monkeypatch):
    sid = add_host()
    src = add_service(sid, "svc-a")
    tgt = add_service(sid, "svc-b")

    r = client.post("/api/v2/topology/cicd/relations", json={
        "scenario": "cicd", "source_service_id": src, "target_service_id": tgt,
        "relation_type": "data_flow", "label": "测试连线",
    })
    assert r.status_code == 200, r.text
    rel_id = r.json()["relation"]["id"]
    assert r.json()["relation"]["scenario"] == "cicd"

    # 重复添加：幂等 upsert（同一关系，更新类型/标签）
    r2 = client.post("/api/v2/topology/cicd/relations", json={
        "scenario": "cicd", "source_service_id": src, "target_service_id": tgt,
        "relation_type": "invoke", "label": "更新",
    })
    assert r2.status_code == 200
    assert r2.json()["relation"]["id"] == rel_id
    assert r2.json()["relation"]["relation_type"] == "invoke"

    # 拓扑接口包含 relation_id 便于删除
    top = client.get("/api/v2/topology?scenario=cicd").json()
    assert any(e.get("relation_id") == rel_id for e in top["edges"])


def test_relation_delete_is_idempotent_and_validates(monkeypatch):
    sid = add_host()
    src = add_service(sid, "svc-a")
    tgt = add_service(sid, "svc-b")

    r = client.post("/api/v2/topology/cicd/relations", json={
        "scenario": "cicd", "source_service_id": src, "target_service_id": tgt,
        "relation_type": "data_flow", "label": "",
    })
    rel_id = r.json()["relation"]["id"]

    d = client.delete(f"/api/v2/topology/relations/{rel_id}")
    assert d.status_code == 200
    assert d.json()["deleted"] is True

    # 幂等：再删提示不存在，不报错
    d2 = client.delete(f"/api/v2/topology/relations/{rel_id}")
    assert d2.status_code == 200
    assert d2.json()["deleted"] is False

    # 非法关系 ID 422
    assert client.delete("/api/v2/topology/relations/not-a-uuid").status_code == 422


def test_relation_validation_errors(monkeypatch):
    sid = add_host()
    src = add_service(sid, "svc-a")

    # 自连接 400
    r = client.post("/api/v2/topology/cicd/relations", json={
        "scenario": "cicd", "source_service_id": src, "target_service_id": src,
        "relation_type": "x", "label": "",
    })
    assert r.status_code == 400

    # 非法服务 ID 422
    r = client.post("/api/v2/topology/cicd/relations", json={
        "scenario": "cicd", "source_service_id": "nope", "target_service_id": src,
        "relation_type": "x", "label": "",
    })
    assert r.status_code == 422

    # wireguard 场景拒绝（path 校验）
    r = client.post("/api/v2/topology/wireguard/relations", json={
        "scenario": "wireguard", "source_service_id": src, "target_service_id": src,
        "relation_type": "x", "label": "",
    })
    assert r.status_code == 400

    # 不存在的服务 404
    r = client.post("/api/v2/topology/cicd/relations", json={
        "scenario": "cicd", "source_service_id": str(uuid.uuid4()), "target_service_id": src,
        "relation_type": "x", "label": "",
    })
    assert r.status_code == 404


def test_service_relations_table_left_unmodified_by_layout():
    """布局保存不得触碰 ServiceRelation 表。"""
    with SessionLocal() as db:
        before = db.query(ServiceRelation).count()
    import tempfile, os
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("TOPOLOGY_LAYOUT_PATH", os.path.join(tempfile.gettempdir(), "layout_test.json"))
    r = client.post("/api/v2/topology/layout", json={"scenario": "gateway", "positions": {"a": {"x": 1, "y": 2}}})
    assert r.status_code == 200
    with SessionLocal() as db:
        assert db.query(ServiceRelation).count() == before
    monkeypatch.undo()