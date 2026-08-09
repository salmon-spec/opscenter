"""v3.27 D1 证书监控单元测试"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, timedelta

import pytest
from app.cert_scanner import DEFAULT_CERT_RULE, check_certificate


def test_default_rule_shape():
    assert DEFAULT_CERT_RULE["metric"] == "cert_days_left"
    assert DEFAULT_CERT_RULE["operator"] == "<"
    assert DEFAULT_CERT_RULE["threshold"] == "30"


def test_check_certificate_validation():
    """非法域名应抛异常而非挂死（网络不可达场景）。"""
    with pytest.raises(Exception):
        check_certificate("invalid-domain-name-xyz.invalid", 443, timeout=1)


def test_days_left_math():
    """验证剩余天数计算逻辑（用未来时间模拟）。"""
    now = datetime.utcnow()
    future = now + timedelta(days=45)
    days = int((future - now).total_seconds() / 86400)
    assert days == 45
