"""主机详情侧栏端点测试（Wave1-BE2）。

运行方式（本地无 PostgreSQL 时先建目录再指定 SQLite）：
    cd backend
    New-Item -ItemType Directory -Force -Path .tmp
    $env:DATABASE_URL = "sqlite:///./.tmp/local-test.db"
    python -m pytest tests/test_server_details.py tests/test_smoke.py -q
"""

import os
import sys
import time
import uuid
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("OPS_AUTH_ENABLED", "false")
os.environ.setdefault("LOCAL_HOST", "127.0.0.1")

sys.path.insert(0, "/opt/opscenter/backend")

from fastapi.testclient import TestClient

from app.agent_tasks import AGENT_TASKS
from app.main import Base, app, engine
from app.database import SessionLocal
from app.models import MetricHistory, MetricRollup, PlazaHealthState, Server, Service
import app.server_details as server_details

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup():
    """每条用例重建全部表，保证干净状态（与 test_smoke 一致）。"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def _create_server(name="H", host="10.0.0.5", **fields):
    payload = {"name": name, "host": host}
    payload.update(fields)
    r = client.post("/api/v2/servers", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _set_server(server_id, **fields):
    with SessionLocal() as db:
        row = db.query(Server).filter(Server.id == uuid.UUID(server_id)).first()
        assert row is not None
        for key, value in fields.items():
            setattr(row, key, value)
        db.commit()


def _add_metrics(server_id, points):
    with SessionLocal() as db:
        uid = uuid.UUID(server_id)
        for metric, minutes_ago, value in points:
            db.add(MetricHistory(
                server_id=uid,
                timestamp=datetime.utcnow() - timedelta(minutes=minutes_ago),
                metric=metric, value=value,
            ))
        db.commit()


# ── overview ─────────────────────────────────────────────────────────────────

OVERVIEW_SERVER_KEYS = {
    "id", "name", "host", "ssh_port", "ssh_user", "status", "tags", "remark",
    "agent_status", "agent_version", "agent_port", "agent_type", "log_agent_status",
    "lan_ip", "wireguard_ip", "preferred_management_channel", "management_address_override",
    "kubernetes_node_name", "node_role", "runtime_type",
    "last_seen", "last_online_at", "last_error",
}


def test_overview_fields_and_speed():
    sid = _create_server(name="OV", host="10.0.0.21",
                         lan_ip="192.168.1.21", wireguard_ip="10.66.66.21",
                         preferred_management_channel="lan", node_role="worker",
                         runtime_type="containerd", remark="详情侧栏")
    _set_server(sid, agent_status="running", agent_version="2.6.2", agent_token="tok")
    _add_metrics(sid, [
        ("cpu", 0, 12.3), ("memory", 0, 45.6), ("disk", 1, 66.7),
        ("load1", 0, 0.42), ("net_rx", 0, 12345.6), ("net_tx", 0, 6789.0),
    ])
    started = time.monotonic()
    r = client.get(f"/api/v2/servers/{sid}/overview")
    elapsed = time.monotonic() - started
    assert r.status_code == 200
    assert elapsed < 2.0  # 300ms 目标的宽松回归线
    data = r.json()
    assert set(data["server"].keys()) == OVERVIEW_SERVER_KEYS
    assert data["server"]["lan_ip"] == "192.168.1.21"
    assert data["server"]["node_role"] == "worker"
    metrics = data["metrics"]
    assert metrics["cpu"] == 12.3
    assert metrics["memory"] == 45.6
    assert metrics["disk"] == 66.7
    assert metrics["load"] == 0.42
    assert metrics["net_in"] == 12345.6
    assert metrics["net_out"] == 6789.0
    assert metrics["ts"]
    assert data["k8s_node"] is None


def test_overview_no_metrics_notes():
    sid = _create_server(host="10.0.0.20")
    data = client.get(f"/api/v2/servers/{sid}/overview").json()
    assert data["metrics"]["cpu"] is None
    assert data["metrics"]["ts"] is None
    assert data["k8s_node"] is None
    assert any("暂无监控数据" in note for note in data["notes"])


def test_overview_id_semantics():
    assert client.get("/api/v2/servers/not-a-uuid/overview").status_code == 422
    assert client.get(f"/api/v2/servers/{uuid.uuid4()}/overview").status_code == 404
    assert client.get("/api/v2/servers/not-a-uuid/connectivity").status_code == 422
    assert client.get(f"/api/v2/servers/{uuid.uuid4()}/services").status_code == 404


def test_overview_k8s_node_none_without_mapping():
    sid = _create_server(host="10.0.0.22")
    data = client.get(f"/api/v2/servers/{sid}/overview").json()
    assert data["k8s_node"] is None
    assert not any("K8s" in note for note in data["notes"])


def test_overview_k8s_node_snapshot_passthrough(monkeypatch):
    sid = _create_server(host="10.0.0.23", kubernetes_node_name="worker2")
    monkeypatch.setattr(
        server_details, "_lookup_k8s_node",
        lambda name: ({"name": name, "status": "Ready", "pods": 12}, None),
    )
    data = client.get(f"/api/v2/servers/{sid}/overview").json()
    assert data["k8s_node"] == {"name": "worker2", "status": "Ready", "pods": 12}


def test_overview_k8s_node_lookup_budget(monkeypatch):
    sid = _create_server(host="10.0.0.24", kubernetes_node_name="worker1")

    def slow_lookup(name):
        time.sleep(2)
        return None, "never"

    monkeypatch.setattr(server_details, "_lookup_k8s_node", slow_lookup)
    started = time.monotonic()
    data = client.get(f"/api/v2/servers/{sid}/overview").json()
    elapsed = time.monotonic() - started
    assert elapsed < 2.0
    assert data["k8s_node"] is None
    assert any("预算" in note for note in data["notes"])


# ── trends ───────────────────────────────────────────────────────────────────

def test_trends_raw_series_and_validation():
    sid = _create_server(host="10.0.0.31")
    points = []
    for i in range(13):
        ago = 180 - i * 15  # 跨 3 小时
        points.append(("cpu", ago, 10.0 + i))
        points.append(("memory", ago, 40.0 + i))
        points.append(("net_rx", ago, 100.0 + i))
        points.append(("net_tx", ago, 200.0 + i))
    _add_metrics(sid, points)

    data = client.get(f"/api/v2/servers/{sid}/metrics/trends?range=6h").json()
    assert data["range"] == "6h"
    assert data["resolution"] == "raw"
    assert data["interval_seconds"] == 0
    assert len(data["series"]["cpu"]) == 13
    assert set(data["series"]["cpu"][0].keys()) == {"t", "v"}
    assert data["series"]["cpu"][-1]["v"] == 22.0
    assert data["series"]["network"]["in"][0]["v"] == 100.0
    assert data["series"]["network"]["out"][0]["v"] == 200.0

    subset = client.get(f"/api/v2/servers/{sid}/metrics/trends?range=1h&metrics=cpu").json()
    assert set(subset["series"].keys()) == {"cpu"}

    assert client.get(f"/api/v2/servers/{sid}/metrics/trends?range=2h").status_code == 422
    assert client.get(f"/api/v2/servers/{sid}/metrics/trends?metrics=foo").status_code == 422
    assert client.get(f"/api/v2/servers/{uuid.uuid4()}/metrics/trends").status_code == 404


def test_trends_aggregated_requires_rollups():
    sid = _create_server(host="10.0.0.32")
    _add_metrics(sid, [("cpu", 60, 50.0)])  # 1 小时前有原始点
    data = client.get(f"/api/v2/servers/{sid}/metrics/trends?range=24h").json()
    assert data["resolution"] == "1h"
    assert data["interval_seconds"] == 3600
    assert data["series"]["cpu"] == []  # 禁止整段拉原始点
    assert data["notes"]


def test_trends_rollup_5m_fallback():
    sid = _create_server(host="10.0.0.33")
    with SessionLocal() as db:
        db.add(MetricRollup(
            server_id=uuid.UUID(sid), metric="cpu", resolution="5m",
            bucket_at=datetime.utcnow() - timedelta(hours=5),
            value_avg=21.5, value_min=20.0, value_max=23.0, sample_count=10,
        ))
        db.commit()
    data = client.get(f"/api/v2/servers/{sid}/metrics/trends?range=24h").json()
    assert data["resolution"] == "5m"
    assert data["interval_seconds"] == 300
    assert data["series"]["cpu"][0]["v"] == 21.5
    assert any("5m" in note for note in data["notes"])


def test_trends_1h_rollup_for_long_range():
    sid = _create_server(host="10.0.0.34")
    with SessionLocal() as db:
        db.add(MetricRollup(
            server_id=uuid.UUID(sid), metric="net_rx", resolution="1h",
            bucket_at=datetime.utcnow() - timedelta(days=2),
            value_avg=321.0, value_min=300.0, value_max=400.0, sample_count=120,
        ))
        db.commit()
    data = client.get(f"/api/v2/servers/{sid}/metrics/trends?range=30d&metrics=network").json()
    assert data["resolution"] == "1h"
    assert data["interval_seconds"] == 3600
    assert data["series"]["network"]["in"][0]["v"] == 321.0
    assert data["series"]["network"]["out"] == []


# ── connectivity ─────────────────────────────────────────────────────────────

def test_connectivity_lan_fail_wg_backup_once(monkeypatch):
    sid = _create_server(host="10.0.0.41", lan_ip="192.168.1.41", wireguard_ip="10.66.66.41")
    calls = []

    def fake_probe(addr, port, timeout=3.0):
        calls.append((addr, port))
        if addr == "192.168.1.41":
            return {"ok": False, "latency_ms": None, "error": "ConnectTimeout: 192.168.1.41:19100"}
        return {"ok": True, "latency_ms": 4.2, "error": None}

    monkeypatch.setattr(server_details, "_probe_agent_health", fake_probe)
    body = client.get(f"/api/v2/servers/{sid}/connectivity").json()
    assert calls == [("192.168.1.41", 19100), ("10.0.0.41", 19100)]  # 备用使用主地址且只探测一次
    assert body["preferred"] == "lan"
    assert body["effective_target"] == "10.0.0.41"
    assert body["lan"]["ok"] is False
    assert body["lan"]["error"] == "ConnectTimeout: 192.168.1.41:19100"
    assert body["wireguard"]["ok"] is True
    assert body["wireguard"]["latency_ms"] == 4.2
    assert body["agent"]["port"] == 19100
    assert any("备用通道" in note for note in body["notes"])
    with SessionLocal() as db:
        row = db.query(Server).filter(Server.id == uuid.UUID(sid)).first()
        assert row.last_lan_probe["ok"] is False
        assert row.last_wg_probe["ok"] is True
        assert "checked_at" in row.last_wg_probe
        assert "latency_ms" in row.last_lan_probe


def test_connectivity_ignores_legacy_wireguard_preference(monkeypatch):
    sid = _create_server(host="10.0.0.42", lan_ip="192.168.1.42",
                         wireguard_ip="10.66.66.42", preferred_management_channel="wireguard")
    calls = []

    def fake_probe(addr, port, timeout=3.0):
        calls.append(addr)
        return {"ok": True, "latency_ms": 1.5, "error": None}

    monkeypatch.setattr(server_details, "_probe_agent_health", fake_probe)
    body = client.get(f"/api/v2/servers/{sid}/connectivity").json()
    assert calls == ["192.168.1.42"]
    assert body["preferred"] == "lan"
    assert body["effective_target"] == "192.168.1.42"
    assert body["lan"]["ok"] is True
    assert body["wireguard"] is None
    with SessionLocal() as db:
        row = db.query(Server).filter(Server.id == uuid.UUID(sid)).first()
        assert row.last_lan_probe["ok"] is True
        assert row.last_wg_probe is None  # 未探测的通道不落库


def test_connectivity_ignores_legacy_override(monkeypatch):
    sid = _create_server(host="10.0.0.43", lan_ip="192.168.1.43",
                         wireguard_ip="10.66.66.43", management_address_override="192.168.1.99")
    calls = []

    def fake_probe(addr, port, timeout=3.0):
        calls.append(addr)
        return {"ok": addr != "192.168.1.99", "latency_ms": 2.0 if addr != "192.168.1.99" else None,
                "error": None if addr != "192.168.1.99" else "connect refused"}

    monkeypatch.setattr(server_details, "_probe_agent_health", fake_probe)
    body = client.get(f"/api/v2/servers/{sid}/connectivity").json()
    assert calls == ["192.168.1.43"]
    assert body["effective_target"] == "192.168.1.43"
    with SessionLocal() as db:
        row = db.query(Server).filter(Server.id == uuid.UUID(sid)).first()
        assert row.last_lan_probe["ok"] is True
        assert row.last_wg_probe is None
        assert set(row.last_lan_probe.keys()) == {"ok", "latency_ms", "error", "checked_at"}


def test_connectivity_both_fail_bounded(monkeypatch):
    sid = _create_server(host="10.0.0.45", lan_ip="192.168.1.45", wireguard_ip="10.66.66.45")
    calls = []

    def fake_probe(addr, port, timeout=3.0):
        calls.append(addr)
        return {"ok": False, "latency_ms": None, "error": "ConnectError"}

    monkeypatch.setattr(server_details, "_probe_agent_health", fake_probe)
    body = client.get(f"/api/v2/servers/{sid}/connectivity").json()
    assert len(calls) == 2  # 绝不双路无限重试
    assert body["effective_target"] == "192.168.1.45"
    assert body["preferred"] == "lan"
    assert body["lan"]["ok"] is False
    assert body["wireguard"]["ok"] is False


def test_connectivity_host_only_single_probe(monkeypatch):
    sid = _create_server(host="10.0.0.46")
    calls = []

    def fake_probe(addr, port, timeout=3.0):
        calls.append(addr)
        return {"ok": True, "latency_ms": 0.8, "error": None}

    monkeypatch.setattr(server_details, "_probe_agent_health", fake_probe)
    body = client.get(f"/api/v2/servers/{sid}/connectivity").json()
    assert calls == ["10.0.0.46"]  # 无 lan_ip：LAN 通道回退 host，仅一次探测
    assert body["preferred"] == "wireguard"
    assert body["effective_target"] == "10.0.0.46"
    assert body["lan"] is None
    assert body["wireguard"]["ok"] is True


def test_connectivity_local_host(monkeypatch):
    sid = _create_server(host="local-test", is_local=True)
    calls = []

    def fake_probe(addr, port, timeout=3.0):
        calls.append(addr)
        return {"ok": True, "latency_ms": 0.3, "error": None}

    monkeypatch.setattr(server_details, "_probe_agent_health", fake_probe)
    body = client.get(f"/api/v2/servers/{sid}/connectivity").json()
    assert calls == [server_details.LOCAL_AGENT_HOST]  # 本机走 resolve_agent_host 语义
    assert len(calls) == 1
    assert body["preferred"] == "lan"
    assert body["wireguard"] is None
    assert any("本机" in note for note in body["notes"])


# ── agent/check ──────────────────────────────────────────────────────────────

def test_agent_check_version_compare(monkeypatch):
    sid = _create_server(host="10.0.0.51")
    _set_server(sid, agent_status="running", agent_version="2.6.1", agent_token="tok")
    monkeypatch.setattr(server_details, "get_agent_version", lambda: "2.6.2")
    monkeypatch.setattr(server_details, "_probe_agent_api",
                        lambda addr, port, token, timeout=3.0: True)
    body = client.post(f"/api/v2/servers/{sid}/agent/check").json()
    assert body["current_version"] == "2.6.1"
    assert body["target_version"] == "2.6.2"
    assert body["outdated"] is True
    assert body["reachable"] is True

    monkeypatch.setattr(server_details, "_probe_agent_api",
                        lambda addr, port, token, timeout=3.0: False)
    body2 = client.post(f"/api/v2/servers/{sid}/agent/check").json()
    assert body2["reachable"] is False
    assert any("在线验证失败" in note for note in body2["notes"])

    _set_server(sid, agent_status="error", agent_token=None)
    body3 = client.post(f"/api/v2/servers/{sid}/agent/check").json()
    assert body3["reachable"] is None
    assert any("未处于 running" in note for note in body3["notes"])

    _set_server(sid, agent_status="running", agent_version="2.6.2")
    body4 = client.post(f"/api/v2/servers/{sid}/agent/check").json()
    assert body4["outdated"] is False

    assert client.post("/api/v2/servers/bogus/agent/check").status_code == 422


# ── agent/upgrade/status ─────────────────────────────────────────────────────

def test_agent_upgrade_status():
    sid = _create_server(host="10.0.0.61")
    body = client.get(f"/api/v2/servers/{sid}/agent/upgrade/status").json()
    assert body["task"] is None
    assert body["agent_status"] == "not_deployed"
    assert "success_criteria" not in body

    task = AGENT_TASKS.start(sid, kind="agent_deploy")
    try:
        body2 = client.get(f"/api/v2/servers/{sid}/agent/upgrade/status").json()
        assert body2["task"]["task_id"] == task["task_id"]
        assert body2["task"]["status"] == "deploying"
        assert body2["task"]["kind"] == "agent_deploy"
        assert body2["success_criteria"] == "新版本进程运行 /health 200 / Bearer 可读"
    finally:
        AGENT_TASKS.finish(sid, success=True, message="Agent 已就绪")
    body3 = client.get(f"/api/v2/servers/{sid}/agent/upgrade/status").json()
    assert body3["task"]["status"] == "success"


# ── services ─────────────────────────────────────────────────────────────────

def test_services_with_plaza_health():
    sid = _create_server(host="10.0.0.71")
    with SessionLocal() as db:
        svc = Service(server_id=uuid.UUID(sid), name="GitLab", url="http://192.168.1.41:80",
                      source="manual", port=80)
        svc2 = Service(server_id=uuid.UUID(sid), name="Jenkins", url="http://192.168.1.41:8080",
                       source="docker_auto", port=8080)
        svc3 = Service(server_id=uuid.UUID(sid), name="Retired", url="http://192.168.1.41:9",
                       source="manual", port=9, hidden=True)
        db.add_all([svc, svc2, svc3])
        db.flush()
        db.add(PlazaHealthState(
            plaza_key=f"manual-{svc.id}", stable_status="up",
            last_transition_at=datetime.utcnow() - timedelta(hours=1),
            last_checked_at=datetime.utcnow(),
        ))
        db.commit()
        gitlab_id = str(svc.id)
    body = client.get(f"/api/v2/servers/{sid}/services").json()
    services = body["services"]
    assert len(services) == 2  # hidden 服务不展示
    by_name = {item["name"]: item for item in services}
    gitlab = by_name["GitLab"]
    assert gitlab["id"] == gitlab_id
    assert gitlab["source"] == "manual"
    assert gitlab["port"] == 80
    assert gitlab["url"] == "http://192.168.1.41:80"
    assert gitlab["health"]["status"] == "up"
    assert gitlab["health"]["last_change_at"]
    assert gitlab["health"]["internal"] is None
    assert gitlab["health"]["external"] is None
    assert by_name["Jenkins"]["health"]["status"] == "unknown"  # 无广场健康态
    assert by_name["Jenkins"]["health"]["last_change_at"] is None
    assert any("双层" in note for note in body["notes"])
    assert any("隐藏" in note for note in body["notes"])
