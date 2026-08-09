"""OpsCenter Alerting Tests (v3.26, F4/F6/F7)

Covers: rule CRUD via API, event ack, engine pure-function evaluation,
seed idempotency, and DEFAULT_RULES metric vocabulary correctness.
"""

import pytest

import os
import sys

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://opscenter:opscenter123@127.0.0.1:5433/opscenter_test",
)
os.environ["OPS_AUTH_ENABLED"] = "false"
os.environ["LOCAL_HOST"] = "127.0.0.1"

# Support both local checkout and CI container layout
sys.path.insert(0, "/opt/opscenter/backend")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.main import app, Base, engine, SessionLocal  # noqa: E402
from app.alerting import (  # noqa: E402
    _evaluate,
    DEFAULT_RULES,
    seed_default_rules,
)
from app.models import AlertRule, AlertEvent  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup():
    """Recreate all tables before each test (clean state)."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


# ---------- Engine pure-function ----------

def test_evaluate_numeric_operators():
    assert _evaluate(_mk_rule(operator=">", threshold="90"), "95") is True
    assert _evaluate(_mk_rule(operator=">", threshold="90"), "85") is False
    assert _evaluate(_mk_rule(operator="<", threshold="90"), "85") is True
    assert _evaluate(_mk_rule(operator=">=", threshold="90"), "90") is True
    assert _evaluate(_mk_rule(operator="<=", threshold="90"), "91") is False
    assert _evaluate(_mk_rule(operator="==", threshold="90"), "90") is True
    assert _evaluate(_mk_rule(operator="!=", threshold="90"), "91") is True


def test_evaluate_string_operators():
    r = _mk_rule(value_type="string", operator="!=", threshold="online")
    assert _evaluate(r, "offline") is True
    assert _evaluate(r, "online") is False
    r2 = _mk_rule(value_type="string", operator="==", threshold="running")
    assert _evaluate(r2, "running") is True


def test_evaluate_no_data():
    """None 值（无采集数据）不应触发告警。"""
    assert _evaluate(_mk_rule(operator=">", threshold="90"), None) is False


def _mk_rule(value_type="numeric", operator=">", threshold="90"):
    # 用真实 AlertRule 实例（仅 _evaluate 读取的三个字段），避免鸭子类型脆弱性
    return AlertRule(value_type=value_type, operator=operator, threshold=threshold)


# ---------- DEFAULT_RULES vocabulary (H1 复查) ----------

def test_default_rules_metric_vocabulary():
    metrics = {r["metric"] for r in DEFAULT_RULES}
    # 数值型指标必须是 metric_history 中真实存储的列名（cpu/memory/disk）
    assert {"cpu", "memory", "disk"}.issubset(metrics)
    # 字符串型指标映射 server.agent_status / server.status，不是虚构字段
    assert "server_status" in metrics
    assert "agent_status" in metrics
    # 字符串阈值用字符串比较，不存在 "up"/"online" 误用
    for r in DEFAULT_RULES:
        if r["value_type"] == "string":
            assert r["threshold"] in ("online", "running")


# ---------- API: rule CRUD ----------

def test_rule_crud():
    # create
    r = client.post("/api/v2/alert-rules", json={
        "name": "CPU过高", "metric": "cpu", "operator": ">", "threshold": "90",
        "duration_sec": 120, "cooldown_sec": 300, "enabled": True,
    })
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    # list
    lst = client.get("/api/v2/alert-rules")
    assert lst.status_code == 200
    assert any(x["id"] == rid for x in lst.json())
    # disable
    u = client.put(f"/api/v2/alert-rules/{rid}", json={"enabled": False})
    assert u.status_code == 200
    # delete
    d = client.delete(f"/api/v2/alert-rules/{rid}")
    assert d.status_code == 200
    lst2 = client.get("/api/v2/alert-rules")
    assert not any(x["id"] == rid for x in lst2.json())


def test_create_rule_requires_name_and_threshold():
    r = client.post("/api/v2/alert-rules", json={"metric": "cpu"})
    # name 与 threshold 缺失 -> 422 校验失败
    assert r.status_code == 422


# ---------- API: event ack ----------

def test_event_ack():
    from app.models import Server
    db = SessionLocal()
    try:
        srv = Server(name="ack-test", host="10.0.0.9", ssh_user="ops",
                     auth_type="password", agent_status="running")
        db.add(srv)
        db.commit()
        db.refresh(srv)
        rule = AlertRule(name="t", metric="cpu", operator=">", threshold="90")
        db.add(rule)
        db.commit()
        db.refresh(rule)
        ev = AlertEvent(rule_id=rule.id, server_id=srv.id, status="firing",
                        current_value="95")
        db.add(ev)
        db.commit()
        db.refresh(ev)
        eid = ev.id
    finally:
        db.close()

    r = client.post(f"/api/v2/alert-events/{eid}/ack")
    assert r.status_code == 200
    assert r.json()["status"] == "acked"

    db = SessionLocal()
    try:
        assert db.query(AlertEvent).get(eid).status == "acked"
        # 清理（保持测试库干净）
        db.query(AlertEvent).filter(AlertEvent.id == eid).delete()
        db.query(AlertRule).filter(AlertRule.name == "t").delete()
        db.query(Server).filter(Server.name == "ack-test").delete()
        db.commit()
    finally:
        db.close()


# ---------- seed idempotency (M4 复查) ----------

def test_seed_default_rules_idempotent():
    seed_default_rules()
    seed_default_rules()  # 第二次不应重复写入
    db = SessionLocal()
    try:
        count = db.query(AlertRule).count()
        assert count == len(DEFAULT_RULES)
    finally:
        db.close()
