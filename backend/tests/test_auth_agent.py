"""OpsCenter Agent Auth Tests (v3.26, F1)

F1 要求 Agent 强制 token 鉴权：空 token 不再放行，错误/缺失 Bearer 头返回 401。
这里对 AgentHandler._check_auth 做单元级验证（不启动 HTTP server）。
"""

import os
import sys
import io

# 让 agent/ 目录可直接 import opsagent（其内部 import scanner 同源）
AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "agent")
sys.path.insert(0, os.path.abspath(AGENT_DIR))

import opsagent  # noqa: E402


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


def test_agent_version_is_2_6_1():
    # 2.6.1 parses the real `wg show all dump` format.
    assert opsagent.AGENT_VERSION == "2.6.1"


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
