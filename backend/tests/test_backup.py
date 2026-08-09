"""v3.27 D3 备份验证 + D4 镜像检测 单元测试"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.backup_scanner import seed_backup_rule
from app.models import AlertRule


def test_backup_seed_idempotent(monkeypatch):
    """seed_backup_rule 在已有规则时不应重复插入（幂等）。"""
    class FakeSession:
        def __init__(self):
            self.added = []

        def query(self, model):
            class Q:
                def filter(self, *a, **k):
                    return self

                def first(self):
                    return AlertRule(metric="backup_age")  # 已存在 -> 应跳过

            return Q()

        def add(self, obj):
            self.added.append(obj)

        def commit(self):
            pass

    sess = FakeSession()
    result = seed_backup_rule(db=sess)
    assert result is False  # 已存在不插入
    assert len(sess.added) == 0


def test_backup_seed_inserts_when_empty(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.added = []

        def query(self, model):
            class Q:
                def filter(self, *a, **k):
                    return self

                def first(self):
                    return None

            return Q()

        def add(self, obj):
            self.added.append(obj)

        def commit(self):
            pass

    sess = FakeSession()
    result = seed_backup_rule(db=sess)
    assert result is True
    assert len(sess.added) == 1
    assert sess.added[0].metric == "backup_age"


def test_backup_rule_shape():
    rule = AlertRule(metric="backup_age", value_type="numeric", operator=">", threshold="24")
    assert rule.metric == "backup_age"
    assert rule.operator == ">"
    assert rule.threshold == "24"
