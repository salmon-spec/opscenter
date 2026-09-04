"""v4.8 P1: unified health screen summary - aggregates, no N+1 semantics, honest missing data."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import Base, SessionLocal, app, engine
from app.models import AlertEvent, DatabaseInstance, Server
from app.topology import _LAST_AGENT_SNAPSHOT, record_agent_snapshot

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    _LAST_AGENT_SNAPSHOT.clear()
    yield
    _LAST_AGENT_SNAPSHOT.clear()


def add_host(name, host, **extra):
    payload = {"name": name, "host": host, "ssh_port": 22, "auto_deploy_agent": False, **extra}
    r = client.post("/api/v2/servers", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_empty_screen_summary_has_all_new_fields_and_zero_is_honest():
    r = client.get("/api/v2/screen/summary")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["generated_at"]
    assert set(data.keys()) >= {
        "generated_at", "freshness", "partial_errors",
        "hosts_summary", "containers_summary", "databases_summary",
        "services_summary", "logs_summary", "wireguard_summary", "alerts_summary",
        "servers", "services", "active_alerts", "trends",
    }
    assert data["hosts_summary"] == {"total": 0, "online": 0, "offline": 0, "stale": 0}
    assert data["containers_summary"] == {"running": 0, "stopped": 0, "unknown_hosts": 0}
    assert data["databases_summary"]["total"] == 0  # 无实例是正常状态，不是错误


def test_summary_uses_agent_snapshot_for_containers_and_marks_missing_metrics_null():
    sid = add_host("node", "10.66.66.20")
    with SessionLocal() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(sid)).first()
        srv.status = "online"
        db.commit()
    record_agent_snapshot(sid, {"container_running": 3, "container_stopped": 1})

    r = client.get("/api/v2/screen/summary")
    data = r.json()
    assert data["hosts_summary"]["total"] == 1
    assert data["hosts_summary"]["online"] == 1
    assert data["containers_summary"]["running"] == 3
    assert data["containers_summary"]["stopped"] == 1
    # 缺失指标必须为 null，不得伪造为 0
    assert data["servers"][0]["cpu"] is None
    assert data["servers"][0]["stale"] is True
    assert data["hosts_summary"]["stale"] == 1
    # 单主机在线但没有 Agent 支持 WG 时，WG 汇总不阻塞整体响应
    assert "wireguard_summary" in data


def test_summary_database_and_alert_summaries():
    sid = add_host("node", "10.66.66.20")
    with SessionLocal() as db:
        srv = db.query(Server).filter(Server.id == uuid.UUID(sid)).first()
        srv.status = "online"
        db.add(DatabaseInstance(server_id=srv.id, name="pg", engine="postgresql",
                                connection_mode="direct", host="127.0.0.1", port=5432,
                                username="u", status="online"))
        db.add(DatabaseInstance(server_id=srv.id, name="mysql", engine="mysql",
                                connection_mode="direct", host="127.0.0.1", port=3306,
                                username="u", status="error"))
        db.add(DatabaseInstance(server_id=srv.id, name="redis", engine="redis",
                                connection_mode="direct", host="127.0.0.1", port=6379,
                                username="u", status="pending"))
        db.commit()

    r = client.get("/api/v2/screen/summary")
    data = r.json()
    ds = data["databases_summary"]
    assert ds == {"total": 3, "connected": 1, "pending": 1, "error": 1}

    # 无告警时 firing=0 而非报错
    assert data["alerts_summary"]["firing"] == 0
