"""v3.29 服务详情/大屏聚合/健康/历史指标测试。

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

from app.control import router as control_router
from app.database import get_db
from app.models import Base, MetricHistory, Server, Service
from app.dashboard import router as dashboard_router


def _make_app() -> FastAPI:
    """组装仅包含 v3.29 新路由的测试应用（main.py 接线前独立验证）。"""
    app = FastAPI()
    app.include_router(dashboard_router)
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


# ── 已退役功能 ──

def test_api_key_management_routes_removed():
    assert client.get("/api/v2/keys").status_code == 404
    assert client.post("/api/v2/keys", json={"name": "x"}).status_code == 404


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


def test_topology_api_removed():
    assert client.get("/api/v2/topology?scenario=cicd").status_code == 404


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
