"""v3.29 T2/T3 测试：开放 API 密钥 + 服务详情/拓扑/大屏聚合/健康/历史指标。

与现有测试一致：使用独立测试库 opscenter_test，每个用例重建表。
运行于 VM2（sys.path 指向 /opt/opscenter/backend）。
"""

import os
import sys

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://opscenter:opscenter123@127.0.0.1:5433/opscenter_test")
os.environ["OPS_AUTH_ENABLED"] = "false"
os.environ["LOCAL_HOST"] = "127.0.0.1"
sys.path.insert(0, "/opt/opscenter/backend")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api_keys import hash_api_key, verify_api_key
from app.api_keys import router as keys_router
from app.control import router as control_router
from app.database import get_db
from app.models import Base, MetricHistory, Server, Service
from app.topology import router as topology_router


def _make_app() -> FastAPI:
    """组装仅包含 v3.29 新路由的测试应用（main.py 接线前独立验证）。"""
    app = FastAPI()
    app.include_router(keys_router)
    app.include_router(topology_router)
    app.include_router(control_router)
    return app


app = _make_app()
client = TestClient(app)

# 测试专用 engine（与 app.database 同配置，仅用于建表）
from app.config import DB_URL  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

_engine = create_engine(DB_URL)


@pytest.fixture(autouse=True)
def setup():
    """每个用例重建表，保证干净状态。"""
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)
    yield


def _add_server(name: str = "测试机", host: str = "10.0.0.1") -> Server:
    with get_db() as db:
        srv = Server(name=name, host=host, agent_type="remote", ssh_user="ops")
        db.add(srv)
        db.commit()
        db.refresh(srv)
        return srv


def _add_service(server: Server, name: str, url: str, category: str = "CI/CD") -> Service:
    with get_db() as db:
        svc = Service(server_id=server.id, name=name, url=url, category=category, source="manual")
        db.add(svc)
        db.commit()
        db.refresh(svc)
        return svc


# ── T2 API 密钥 ──

def test_create_and_list_api_key():
    r = client.post("/api/v2/keys", json={"name": "大屏读取", "scope": "read"})
    assert r.status_code == 201
    data = r.json()
    assert data["api_key"].startswith("oc_rt_")
    assert data["prefix"] == data["api_key"][:10]

    r = client.get("/api/v2/keys")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["name"] == "大屏读取"
    assert "key_hash" not in rows[0]
    assert "api_key" not in rows[0]


def test_create_key_scope_validation():
    r = client.post("/api/v2/keys", json={"name": "非法", "scope": "admin"})
    assert r.status_code == 400


def test_verify_api_key_hash_roundtrip():
    with get_db() as db:
        from app.models import ApiKey
        row = ApiKey(name="t", key_hash=hash_api_key("oc_rt_secret"), prefix="oc_rt_secr", scope="read")
        db.add(row)
        db.commit()
    assert verify_api_key("oc_rt_secret") is not None
    assert verify_api_key("oc_rt_wrong") is None


def test_delete_api_key():
    r = client.post("/api/v2/keys", json={"name": "待删", "scope": "write"})
    key_id = r.json()["id"]
    r = client.delete(f"/api/v2/keys/{key_id}")
    assert r.status_code == 200
    assert client.get("/api/v2/keys").json() == []
    assert client.delete("/api/v2/keys/00000000-0000-0000-0000-000000000000").status_code == 404


# ── T3 服务详情 ──

def test_service_detail():
    srv = _add_server()
    svc = _add_service(srv, "GitLab", "http://10.66.66.4:8082", "代码与CI/CD")
    r = client.get(f"/api/v2/services/{svc.id}/detail")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "GitLab"
    assert data["server"]["name"] == "测试机"
    assert data["relations"]["outgoing"] == []
    assert data["running_seconds"] is None


def test_service_detail_not_found():
    assert client.get("/api/v2/services/not-a-uuid/detail").status_code == 404
    assert client.get("/api/v2/services/00000000-0000-0000-0000-000000000000/detail").status_code == 404


# ── T3 拓扑 ──

def test_topology_seed_and_idempotent():
    srv = _add_server()
    _add_service(srv, "GitLab", "http://x:8082")
    _add_service(srv, "Jenkins", "http://x:8080")
    _add_service(srv, "Nexus", "http://x:8081")

    r = client.get("/api/v2/topology?scenario=cicd")
    assert r.status_code == 200
    data = r.json()
    names = {n["name"] for n in data["nodes"]}
    assert "GitLab" in names and "Jenkins" in names and "Nexus" in names
    assert len(data["edges"]) >= 2
    first_edges = len(data["edges"])

    # 再次调用不产生重复边（幂等）
    r2 = client.get("/api/v2/topology?scenario=cicd")
    assert len(r2.json()["edges"]) == first_edges


def test_topology_bad_scenario():
    assert client.get("/api/v2/topology?scenario=hack").status_code == 400


def test_topology_empty_db():
    r = client.get("/api/v2/topology?scenario=monitoring")
    assert r.status_code == 200
    assert r.json()["nodes"] == []
    assert r.json()["edges"] == []


# ── T3 大屏聚合 / 健康 / 历史 ──

def test_screen_summary_empty_db():
    r = client.get("/api/v2/screen/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["servers"] == []
    assert data["services"] == []
    assert data["active_alerts"] == []
    assert "cpu" in data["trends"]


def test_services_health():
    srv = _add_server()
    _add_service(srv, "Grafana", "http://x:3000", "监控")
    r = client.get("/api/v2/services/health")
    assert r.status_code == 200
    data = r.json()
    assert data["summary"]["total"] == 1
    assert data["services"][0]["name"] == "Grafana"


def test_monitor_history():
    srv = _add_server()
    with get_db() as db:
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        for i in range(3):
            db.add(
                MetricHistory(
                    server_id=srv.id,
                    metric="cpu",
                    value=10 + i,
                    timestamp=now - timedelta(minutes=3 - i),
                )
            )
        db.commit()
    r = client.get(f"/api/v2/monitor/history?host={srv.name}&metric=cpu")
    assert r.status_code == 200
    points = r.json()["points"]
    assert len(points) == 3
    assert [p["value"] for p in points] == [10.0, 11.0, 12.0]

    assert client.get("/api/v2/monitor/history?host=不存在&metric=cpu").status_code == 404
    assert client.get("/api/v2/monitor/history?host=xx&metric=cpu&start=bad").status_code == 400
    assert client.get("/api/v2/monitor/history?metric=cpu").status_code == 422  # host 必填
