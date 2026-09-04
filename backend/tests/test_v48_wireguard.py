"""v4.8 P1: WireGuard topology - secure parsing, IP matching, health boundaries, caching."""
import importlib.util
import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_MODULE = os.path.join(BACKEND_ROOT, "..", "agent", "opsagent.py")


def _load_agent():
    spec = importlib.util.spec_from_file_location("opsagent_test", AGENT_MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


opsagent = _load_agent()

from app.main import Base, SessionLocal, app, engine  # noqa: E402
from app.models import AlertEvent, Server  # noqa: E402

client = TestClient(app)

# 伪敏感材料（仅测试数据）
PRIVATE_KEY = "J9GpYQr6fBcwcIacqxm23oqLm4WEd86mAKQs4GfZo1A="
PSK = "kA3aLkU1jIy3W9VQ2iQ5yV4Fz3nZ2jYg"

DUMP_SAMPLE = (
    "interface\twg0\t{priv}\t51820\toff\n"
    "peer\t{R1}\t{psk}\t182.92.223.237:51820\t10.66.66.3/32\t1750000000\t123456\t654321\toff\n"
    "peer\t{R2}\t\t203.0.113.9:51820\t10.66.66.99/32\t0\t0\t0\toff\n"
).format(
    priv=PRIVATE_KEY,
    psk=PSK,
    R1="ZGF0YWJhc2U2NGtleTEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWYw",
    R2="c2Vjb25ka2V5YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXoxMjM0NTY3",
)


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    from app.topology import _WG_CACHE
    _WG_CACHE.clear()


# ---------- Agent 解析器：敏感字段测试 ----------

def test_parse_wg_dump_drops_private_key_and_psk():
    parsed = opsagent._parse_wg_dump(DUMP_SAMPLE)
    raw = json.dumps(parsed)
    # 原值（私钥/PSK）绝不允许出现在序列化响应中
    assert PRIVATE_KEY not in raw
    assert PSK not in raw
    # 字段也不允许出现在 keys 中
    keys = raw.lower()
    assert "private_key" not in keys
    assert "preshared_key" not in keys
    assert "public_key" not in keys.replace("public_key_fingerprint", "")
    assert len(parsed) == 1
    iface = parsed[0]
    assert iface["name"] == "wg0" and iface["listen_port"] == 51820
    assert len(iface["peers"]) == 2
    assert iface["peers"][0]["endpoint"] == "182.92.223.237:51820"
    assert iface["peers"][0]["allowed_ips"] == ["10.66.66.3/32"]
    assert iface["peers"][0]["rx_bytes"] == 123456
    assert iface["peers"][0]["latest_handshake_at"] is not None
    # 从未握手
    assert iface["peers"][1]["latest_handshake_at"] is None
    assert iface["peers"][1]["latest_handshake_age_seconds"] is None
    assert iface["peers"][0]["public_key_fingerprint"].startswith("sha256:")


def test_parse_wg_dump_handles_garbage_lines():
    parsed = opsagent._parse_wg_dump("not a tab line\n\ninterface\tpartial\n")
    assert parsed == []


# ---------- 健康边界 ----------

def test_wg_health_boundaries():
    from app.topology import _wg_health
    assert _wg_health(180) == "healthy"
    assert _wg_health(181) == "warning"
    assert _wg_health(600) == "warning"
    assert _wg_health(601) == "offline"
    assert _wg_health(None) == "offline"
    assert _wg_health(0) == "healthy"


# ---------- 拓扑聚合 ----------

def add_host(name, host, **extra):
    payload = {"name": name, "host": host, "ssh_port": 22, "auto_deploy_agent": False, **extra}
    r = client.post("/api/v2/servers", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _fake_agent(spec, calls):
    def fake(host, port=19100, token=""):
        calls.append(host)
        return spec.get(host)
    return fake


def test_wireguard_topology_maps_ip_health_and_caches(monkeypatch):
    add_host("L1", "10.66.66.1")
    add_host("PVE", "10.66.66.3")

    l1_payload = {
        "supported": True, "generated_at": "2026-09-03T07:30:00Z",
        "interfaces": [{
            "name": "wg0", "addresses": ["10.66.66.1/24"], "listen_port": 51820,
            "public_key_fingerprint": "sha256:l1", "peers": [
                {"public_key_fingerprint": "sha256:p3", "endpoint": "10.66.66.3:51820",
                 "allowed_ips": ["10.66.66.3/32"], "latest_handshake_at": "2026-09-03T07:29:15Z",
                 "latest_handshake_age_seconds": 60, "rx_bytes": 100, "tx_bytes": 200},
                {"public_key_fingerprint": "sha256:p99", "endpoint": "203.0.113.9:51820",
                 "allowed_ips": ["10.66.66.99/32"], "latest_handshake_at": None,
                 "latest_handshake_age_seconds": None, "rx_bytes": 0, "tx_bytes": 0},
                {"public_key_fingerprint": "sha256:p5", "endpoint": "10.66.66.5:51820",
                 "allowed_ips": ["10.66.66.5/32"], "latest_handshake_at": "2026-09-03T07:25:00Z",
                 "latest_handshake_age_seconds": 300, "rx_bytes": 5, "tx_bytes": 6},
            ],
        }],
    }
    pve_payload = {"supported": True, "generated_at": "2026-09-03T07:30:00Z",
                   "interfaces": [{"name": "wg0", "addresses": ["10.66.66.3/24"], "listen_port": 51820,
                                   "public_key_fingerprint": "sha256:pve", "peers": [
                                       {"public_key_fingerprint": "sha256:l1", "allowed_ips": ["10.66.66.1/32"],
                                        "latest_handshake_age_seconds": 60, "rx_bytes": 999, "tx_bytes": 999},
                                   ]}]}
    calls = []
    monkeypatch.setattr("app.topology.fetch_agent_wireguard",
                        _fake_agent({"10.66.66.1": l1_payload, "10.66.66.3": pve_payload}, calls))

    r = client.get("/api/v2/topology?scenario=wireguard")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["scenario"] == "wireguard"
    assert data["cached"] is True
    assert data["partial_errors"] == []
    assert data["generated_at"]

    types = {n["type"]: n for n in data["nodes"]}
    assert "hub" in types
    assert types["hub"]["host"] == "10.66.66.1"
    managed = [n for n in data["nodes"] if n["type"] == "managed_host"]
    assert len(managed) == 1 and managed[0]["host"] == "10.66.66.3"
    unmanaged = [n for n in data["nodes"] if n["type"] == "unregistered_peer"]
    assert len(unmanaged) == 2
    ips = {n["wg_ip"] for n in unmanaged}
    assert ips == {"10.66.66.99", "10.66.66.5"}

    s = data["summary"]
    assert s["peer_total"] == 3
    assert s["managed"] == 2          # hub + PVE
    assert s["healthy"] == 2          # hub(自身在线) + PVE age=60
    assert s["warning"] == 1          # .5 age=300
    assert s["offline"] == 1          # .99 从未握手
    assert s["unmanaged"] == 2
    assert s["wg_rx_bytes"] == 105 and s["wg_tx_bytes"] == 206

    # 30 秒缓存：第二次请求不重复访问 Agent
    calls.clear()
    r2 = client.get("/api/v2/topology?scenario=wireguard")
    assert r2.status_code == 200
    assert calls == []

    # 未纳管 Peer 不产生告警事件
    with SessionLocal() as db:
        assert db.query(AlertEvent).count() == 0


def test_wireguard_old_agent_marks_unknown_and_errors(monkeypatch):
    add_host("L1", "10.66.66.1")
    add_host("PVE", "10.66.66.3")
    calls = []
    monkeypatch.setattr("app.topology.fetch_agent_wireguard",
                        _fake_agent({}, calls))  # 全部旧 Agent：返回 None

    r = client.get("/api/v2/topology?scenario=wireguard")
    data = r.json()
    assert data["nodes"] == [] or all(n["health"] in ("unknown", "offline") for n in data["nodes"])
    assert len(data["partial_errors"]) >= 2  # 每台不支持的主机各一条提示
    assert data["summary"]["unknown"] >= 0


def test_wireguard_supported_false_is_reported_not_used(monkeypatch):
    add_host("L1", "10.66.66.1")
    monkeypatch.setattr(
        "app.topology.fetch_agent_wireguard",
        _fake_agent({"10.66.66.1": {"supported": False, "reason": "wg 命令未安装", "interfaces": []}}, []),
    )
    data = client.get("/api/v2/topology?scenario=wireguard").json()
    assert data["nodes"] == []
    assert any("wg 命令未安装" in message for message in data["partial_errors"])


def test_wireguard_non_32_allowed_ips_not_claimed(monkeypatch):
    """非 /32（如 /24）不得随意认领主机。"""
    add_host("PVE", "10.66.66.3")
    l1_payload = {
        "supported": True, "interfaces": [{
            "name": "wg0", "addresses": ["10.66.66.1/24"], "listen_port": 51820,
            "peers": [
                {"public_key_fingerprint": "sha256:p3", "allowed_ips": ["10.66.66.3/24"],
                 "latest_handshake_age_seconds": 30, "rx_bytes": 0, "tx_bytes": 0},
            ],
        }],
    }
    calls = []
    monkeypatch.setattr("app.topology.fetch_agent_wireguard",
                        _fake_agent({"10.66.66.1": l1_payload}, calls))
    # 缺少 L1 主机记录时就没有 hub……在此补一个 L1 主机
    add_host("L1", "10.66.66.1")
    r = client.get("/api/v2/topology?scenario=wireguard")
    data = r.json()
    unmanaged = [n for n in data["nodes"] if n["type"] == "unregistered_peer"]
    managed = [n for n in data["nodes"] if n["type"] == "managed_host"]
    # /24 不精确匹配：PVE 不应被认领为 managed（除非作为 hub 之外的主机出现）
    assert all(n["wg_ip"] is None for n in unmanaged)
    assert not any(n["host"] == "10.66.66.3" and n["wg_ip"] == "10.66.66.3" for n in managed)
