"""v3.27 S1 告警静默单元测试"""
import os, sys, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, timedelta

import pytest
from app.models import AlertRule, AlertSilence, Server
from app import alerting


@pytest.fixture
def fake_rule():
    return AlertRule(id=uuid.uuid4(), name="test", metric="cpu", value_type="numeric",
                     operator=">", threshold="90", duration_sec=60, cooldown_sec=300,
                     enabled=True)


@pytest.fixture
def fake_server():
    return Server(id=uuid.uuid4(), name="srv", host="10.0.0.1", agent_status="running")


class FakeDB:
    """内存版 db，仅支持 _is_silenced 需要的查询。"""
    def __init__(self, silences):
        self._silences = silences

    def query(self, model):
        return _FakeQuery(self, model)

    def scalar(self):
        return False  # 默认无命中


class _FakeQuery:
    def __init__(self, db, model):
        self._db = db
        self._model = model

    def filter(self, *args):
        return self

    def filter_by(self, **kw):
        return self

    def first(self):
        return None

    def all(self):
        return []

    def exists(self):
        return _Exists(self)

    def scalar(self):
        return False


class _Exists:
    def __init__(self, q):
        self.q = q

    def scalar(self):
        return False


def test_evaluate_numeric():
    rule = AlertRule(metric="cpu", value_type="numeric", operator=">", threshold="90")
    assert alerting._evaluate(rule, "95") is True
    assert alerting._evaluate(rule, "85") is False


def test_evaluate_string():
    rule = AlertRule(metric="server_status", value_type="string", operator="!=", threshold="online")
    assert alerting._evaluate(rule, "offline") is True
    assert alerting._evaluate(rule, "online") is False


def test_evaluate_none():
    rule = AlertRule(metric="cpu", value_type="numeric", operator=">", threshold="90")
    assert alerting._evaluate(rule, None) is False


def test_operators():
    for op, a, b, exp in [(">", "5", "3", True), ("<", "5", "3", False),
                          (">=", "5", "5", True), ("<=", "5", "5", True),
                          ("==", "5", "5", True), ("!=", "5", "4", True)]:
        rule = AlertRule(metric="cpu", value_type="numeric", operator=op, threshold=b)
        assert alerting._evaluate(rule, a) is exp, f"op={op}"


def test_resolve_webhooks_merge():
    class R:
        notify_webhooks = ["https://a", "https://b"]
    old_default = alerting.DEFAULT_NOTIFY_WEBHOOKS
    alerting.DEFAULT_NOTIFY_WEBHOOKS = ["https://b", "https://c"]
    try:
        urls = alerting._resolve_webhooks(R())
        assert urls == ["https://a", "https://b", "https://c"]
    finally:
        alerting.DEFAULT_NOTIFY_WEBHOOKS = old_default


def test_resolve_webhooks_empty():
    class R:
        notify_webhooks = []
    old_default = alerting.DEFAULT_NOTIFY_WEBHOOKS
    alerting.DEFAULT_NOTIFY_WEBHOOKS = []
    try:
        assert alerting._resolve_webhooks(R()) == []
    finally:
        alerting.DEFAULT_NOTIFY_WEBHOOKS = old_default
