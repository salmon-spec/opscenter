"""WireGuard agent parser and health threshold checks."""
import importlib.util
import json
import os
from types import SimpleNamespace

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_MODULE = os.path.join(BACKEND_ROOT, "..", "agent", "opsagent.py")


def _load_agent():
    spec = importlib.util.spec_from_file_location("opsagent_test", AGENT_MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


opsagent = _load_agent()

# 伪敏感材料（仅测试数据）
PRIVATE_KEY = "J9GpYQr6fBcwcIacqxm23oqLm4WEd86mAKQs4GfZo1A="
PSK = "kA3aLkU1jIy3W9VQ2iQ5yV4Fz3nZ2jYg"

DUMP_SAMPLE = (
    "wg0\t{priv}\t{pub}\t51820\toff\n"
    "wg0\t{R1}\t{psk}\t182.92.223.237:51820\t10.66.66.3/32\t1750000000\t123456\t654321\toff\n"
    "wg0\t{R2}\t(none)\t203.0.113.9:51820\t10.66.66.99/32\t0\t0\t0\toff\n"
).format(
    priv=PRIVATE_KEY,
    pub="aW50ZXJmYWNlcHVibGlja2V5MTIzNDU2Nzg5YWJjZGVmMDEyMzQ1Njc4OTA=",
    psk=PSK,
    R1="ZGF0YWJhc2U2NGtleTEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWYw",
    R2="c2Vjb25ka2V5YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXoxMjM0NTY3",
)


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
    from app.dashboard import _wg_health
    assert _wg_health(180) == "healthy"
    assert _wg_health(181) == "warning"
    assert _wg_health(600) == "warning"
    assert _wg_health(601) == "offline"
    assert _wg_health(None) == "offline"
    assert _wg_health(0) == "healthy"


def test_wireguard_health_summary_keeps_counts_without_graph_payload(monkeypatch):
    from app import dashboard

    hosts = [
        SimpleNamespace(id="hub", name="L1", host="10.66.66.1", lan_ip="", agent_port=19100,
                        agent_token="", agent_type="remote", status="online"),
        SimpleNamespace(id="managed", name="PVE", host="10.66.66.3", lan_ip="", agent_port=19100,
                        agent_token="", agent_type="remote", status="online"),
    ]

    class FakeDb:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def query(self, _model):
            return SimpleNamespace(all=lambda: hosts)

    monkeypatch.setattr(dashboard, "get_db", FakeDb)
    monkeypatch.setattr(dashboard, "_wg_fetch_cached", lambda sid, host, *_: {
        "hub": {"supported": True, "interfaces": [{"peers": [
            {"allowed_ips": ["10.66.66.3/32"], "latest_handshake_age_seconds": 60,
             "rx_bytes": 10, "tx_bytes": 20},
            {"public_key_fingerprint": "sha256:unknown", "allowed_ips": ["10.66.66.99/32"],
             "latest_handshake_age_seconds": None, "rx_bytes": 1, "tx_bytes": 2},
        ]}]},
        "managed": {"supported": True, "interfaces": [{"peers": []}]},
    }[sid])

    result = dashboard._build_wireguard_summary()
    assert result["summary"] == {
        "peer_total": 2, "managed": 2, "healthy": 2, "warning": 0,
        "offline": 1, "unknown": 0, "unmanaged": 1, "wg_rx_bytes": 11, "wg_tx_bytes": 22,
    }
    assert "nodes" not in result and "edges" not in result
    assert result["summary"]["managed"] == 2
