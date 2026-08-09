"""v3.28 A1/A2 操作审计单元测试"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.audit import _classify, SKIP_PREFIXES


def test_classify_create_silence():
    action, res = _classify("/api/v2/alert-silences", "POST")
    assert action == "create"
    assert res == "silence"


def test_classify_delete_cert():
    action, res = _classify("/api/v2/cert-checks/abc-123", "DELETE")
    assert action == "delete"
    assert res == "cert"


def test_classify_generate_report():
    action, res = _classify("/api/v2/reports/generate", "POST")
    assert action == "generate"
    assert res == "report"


def test_classify_update_rule():
    action, res = _classify("/api/v2/alert-rules/abc-123", "PUT")
    assert action == "update"
    assert res == "alert-rule"


def test_classify_ack_event():
    action, res = _classify("/api/v2/alert-events/abc/ack", "POST")
    assert action == "update"
    assert res == "alert-event"


def test_classify_unknown():
    action, res = _classify("/api/v2/weird/endpoint", "POST")
    assert res == "other"


def test_classify_get_read_ignored():
    """读操作（GET）不审计——classify 仅对写操作调用。"""
    action, res = _classify("/api/v2/servers", "GET")
    # GET 不在映射中，回退为 method.lower()，但中间件不会对 GET 调用
    assert action == "get"


def test_skip_prefixes():
    """白名单路径应被跳过（防递归/噪音）。"""
    for p in SKIP_PREFIXES:
        assert p.startswith("/api/v2/")
    assert any("/health" in p for p in SKIP_PREFIXES)
    assert any("/audit-logs" in p for p in SKIP_PREFIXES)
    assert any("/status-page" in p for p in SKIP_PREFIXES)
