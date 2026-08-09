"""v3.28 R1/R2 巡检日报单元测试"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, timedelta

import pytest

from app.report_engine import _build_markdown, _servers_section, _alerts_section


@pytest.fixture
def fake_sections():
    """构造各 section 的最小化输入（直接测模板函数，不依赖 DB）。"""
    return {
        "servers": {"total": 3, "online": 3, "markdown": "- VM-2：在线"},
        "alerts": {"firing": 1, "fired_yesterday": 2, "recovered_yesterday": 1,
                   "markdown": "- CPU 过高 @ VM-2"},
        "certs": {"total": 1, "expiring_30d": 1, "expired": 0, "markdown": "- example.com：剩余 10 天"},
        "logs": {"matched_rules": 1, "total_matches": 5, "top_rule": "test",
                 "markdown": "- test @ VM-2：命中 5 条"},
        "backups": {"total": 1, "stale": 0, "markdown": "- 备份正常"},
        "images": {"total": 13, "outdated": 0, "markdown": "- 无落后镜像"},
        "services": {"total": 30, "up": 29, "down": 1, "markdown": "- svc @ VM-2"},
    }


def test_build_markdown_contains_all_sections(fake_sections):
    md = _build_markdown(fake_sections)
    assert "服务器" in md
    assert "告警" in md
    assert "证书" in md
    assert "日志异常" in md
    assert "备份" in md
    assert "镜像" in md
    assert "服务" in md


def test_build_markdown_summary_numbers(fake_sections):
    md = _build_markdown(fake_sections)
    assert "3/3 在线" in md
    assert "活跃：**1**" in md
    assert "13 容器" in md
    assert "29/30 在线" in md


def test_build_markdown_empty_sections():
    """空 section 时模板不报错。"""
    empty = {k: {"total": 0, "online": 0, "firing": 0, "fired_yesterday": 0,
                 "recovered_yesterday": 0, "expiring_30d": 0, "expired": 0,
                 "matched_rules": 0, "total_matches": 0, "stale": 0,
                 "outdated": 0, "up": 0, "down": 0, "markdown": "- 无数据"}
             for k in ["servers", "alerts", "certs", "logs", "backups", "images", "services"]}
    md = _build_markdown(empty)
    assert "0/0 在线" in md


def test_build_markdown_daily_report_header(fake_sections):
    md = _build_markdown(fake_sections)
    assert "OpsCenter 巡检日报" in md
