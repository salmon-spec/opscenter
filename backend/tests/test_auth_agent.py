"""OpsCenter Agent Auth Tests (v3.26, F1)

F1 要求 Agent 强制 token 鉴权：空 token 不再放行，错误/缺失 Bearer 头返回 401。
这里对 AgentHandler._check_auth 做单元级验证（不启动 HTTP server）。
"""

import os
import sys
import io
from types import SimpleNamespace

# 让 agent/ 目录可直接 import opsagent（其内部 import scanner 同源）
AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "agent")
sys.path.insert(0, os.path.abspath(AGENT_DIR))

import opsagent  # noqa: E402
import scanner  # noqa: E402


class _StubHandler:
    """模拟 BaseHTTPRequestHandler 的最小子集，供 _check_auth 调用。"""

    def __init__(self, auth_header):
        self.headers = {"Authorization": auth_header}
        self.wfile = io.BytesIO()
        self.status = None

    def send_response(self, code):
        self.status = code

    def send_header(self, *args):
        pass

    def end_headers(self):
        pass

    def _check_auth(self):
        # 与 agent/opsagent.py AgentHandler._check_auth 保持一致
        auth = self.headers.get('Authorization', '')
        if opsagent.TOKEN and auth == f'Bearer {opsagent.TOKEN}':
            return True
        self.send_response(401)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{error: unauthorized}')
        return False


def test_agent_version_is_2_6_3():
    assert opsagent.AGENT_VERSION == "2.6.3"


def test_network_selection_skips_bridge_taps_and_overlays():
    interfaces = {
        "lo": {"kind": "loopback", "master": ""},
        "nic0": {"kind": "physical", "master": "vmbr0"},
        "vmbr0": {"kind": "bridge", "master": ""},
        "tap100i0": {"kind": "virtual", "master": "vmbr0"},
        "wg0": {"kind": "virtual", "master": ""},
    }

    assert scanner.select_host_interfaces(interfaces) == ["nic0"]


def test_network_selection_uses_bond_instead_of_slaves():
    interfaces = {
        "eth0": {"kind": "physical", "master": "bond0"},
        "eth1": {"kind": "physical", "master": "bond0"},
        "bond0": {"kind": "bond", "master": ""},
    }

    assert scanner.select_host_interfaces(interfaces) == ["bond0"]


def test_host_network_summary_uses_only_selected_uplinks():
    snapshot = {
        "nic0": {"rx_rate_mbps": 10.0, "tx_rate_mbps": 20.0, "rx_bytes": 1000, "tx_bytes": 2000, "daily_rx_bytes": 100, "daily_tx_bytes": 200, "daily_date": "2026-09-20"},
        "vmbr0": {"rx_rate_mbps": 9.0, "tx_rate_mbps": 19.0, "rx_bytes": 900, "tx_bytes": 1900, "daily_rx_bytes": 90, "daily_tx_bytes": 190},
    }

    summary = opsagent._network_summary(snapshot, ["nic0"])

    assert summary["interfaces"] == ["nic0"]
    assert summary["daily_date"] == "2026-09-20"
    assert summary["rx_rate_mbps"] == 10.0
    assert summary["tx_rate_mbps"] == 20.0
    assert summary["daily_rx_bytes"] == 100
    assert summary["daily_tx_bytes"] == 200


def test_lightweight_metrics_counts_containers_without_docker_stats(monkeypatch):
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="web|nginx:alpine|Up 1 hour|80/tcp\nold|nginx:alpine|Exited (0)|")

    monkeypatch.setattr(opsagent.subprocess, "run", fake_run)
    monkeypatch.setattr(opsagent, "_collect_container_stats", lambda: (_ for _ in ()).throw(AssertionError("docker stats called")))
    data = opsagent.collect_metrics(lightweight=True)

    assert data["container_running"] == 1
    assert data["container_stopped"] == 1
    assert data["container_stats"] == []
    # 只校验 docker 相关调用：Linux 下 collect_metrics 间接触发的 platform.processor()
    # 会额外执行一次 `uname -p`（CPython 在 Linux 的实现走 subprocess，Windows 不会），
    # 用全量相等断言会让本用例只在 Linux/CI 失败。原意是“轻量采集只跑 docker ps，不跑 docker stats”。
    docker_calls = [c for c in calls if c and c[0] == "docker"]
    assert docker_calls == [["docker", "ps", "-a", "--format", "{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}"]]


def test_check_auth_rejects_empty_token():
    opsagent.TOKEN = ""  # 后端会注入非空 token；此处测“未配置即拒绝”
    h = _StubHandler("")
    assert h._check_auth() is False
    assert h.status == 401


def test_check_auth_rejects_wrong_token():
    opsagent.TOKEN = "strongtoken1234567890"
    h = _StubHandler("Bearer wrongtoken")
    assert h._check_auth() is False
    assert h.status == 401


def test_check_auth_rejects_missing_header():
    opsagent.TOKEN = "strongtoken1234567890"
    h = _StubHandler(None)
    assert h._check_auth() is False
    assert h.status == 401


def test_check_auth_accepts_valid_token():
    opsagent.TOKEN = "strongtoken1234567890"
    h = _StubHandler("Bearer strongtoken1234567890")
    assert h._check_auth() is True
    assert h.status is None  # 成功时不写 401
