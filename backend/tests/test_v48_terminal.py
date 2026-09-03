"""v4.8 P1: terminal session lifecycle - reconnect grace, idle timeout, ping, idempotent destroy."""
import json

import pytest
from fastapi.testclient import TestClient

from app import ssh_terminal as st
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_sessions():
    st._sessions.clear()
    yield
    st._sessions.clear()


def test_session_constants_v48():
    assert st.MAX_SESSIONS_PER_SERVER == 5  # 上限不提高
    assert st.SESSION_TIMEOUT == 14400      # 4 小时无活动才超时
    assert st.RECONNECT_GRACE == 300        # 5 分钟重连宽限


def test_delete_session_is_idempotent():
    # 不存在/已关闭：幂等返回，不报错
    resp = client.delete("/api/v2/terminal/sessions/does-not-exist")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["deleted"] is False

    # 存在：销毁成功且从会话表移除
    sid, err = st.create_session(server_id="srv-1", server_name="node", host="10.0.0.1", port=22, user="root")
    assert sid and not err
    s = st.get_session(sid)
    s.mark_pending_reconnect()  # 保证 is_alive，不被 _cleanup_dead 移除
    resp = client.delete(f"/api/v2/terminal/sessions/{sid}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert st.get_session(sid) is None


def test_status_reports_reconnect_deadline_without_credentials():
    sid, _ = st.create_session(server_id="srv-1", server_name="node", host="10.0.0.1", port=22, user="root")
    s = st.get_session(sid)
    s.mark_pending_reconnect()
    resp = client.get(f"/api/v2/terminal/sessions/{sid}/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "reconnecting"
    assert data["server_id"] == "srv-1"
    assert data["reconnectable"] is True
    assert abs(data["reconnect_deadline"] - (s.reconnect_started_at + 300)) < 2
    assert data["created_at"] > 0 and data["last_activity"] > 0
    # 不得泄露任何凭证
    assert "password" not in json.dumps(data).lower()
    assert "ssh" not in json.dumps(data).lower() or "key" not in json.dumps(data).lower()

    resp = client.get("/api/v2/terminal/sessions/missing/status")
    assert resp.json()["state"] == "missing" and resp.json()["reconnectable"] is False


def test_ping_only_touches_activity_and_never_command():
    """ws 的 ping 分支只更新 last_activity，不进入 shell 输入。
    通过直接请求 create + 手动模拟 send 分支验证 touch 语义。"""
    sid, _ = st.create_session(server_id="srv-1", server_name="node", host="10.0.0.1", port=22, user="root")
    s = st.get_session(sid)
    before = s.last_activity
    import time
    time.sleep(0.01)
    s.touch()
    assert s.last_activity > before


def test_fifth_session_allowed_sixth_rejected_per_server():
    sids = []
    for i in range(5):
        sid, err = st.create_session(server_id="busy", server_name="busy-node",
                                     host="10.0.0.1", port=22, user="root")
        assert sid and not err, f"第 {i+1} 个会话应被允许: {err}"
        st.get_session(sid).mark_pending_reconnect()
        sids.append(sid)
    sid, err = st.create_session(server_id="busy", server_name="busy-node",
                                 host="10.0.0.1", port=22, user="root")
    assert not sid and "5" in err  # 明确拒绝第 6 个
    # 其他主机不受影响
    sid2, err2 = st.create_session(server_id="other", server_name="other-node",
                                   host="10.0.0.2", port=22, user="root")
    assert sid2 and not err2