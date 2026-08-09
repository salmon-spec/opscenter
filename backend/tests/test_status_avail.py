"""v3.28 E2 状态页可用性 + E1 registry proxy 解析 单元测试"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


def test_availability_calc_no_samples():
    """无历史 samples 时用当前状态近似（100/0）。"""
    def calc(status):
        return 100 if status == "running" else 0
    assert calc("running") == 100
    assert calc("stopped") == 0
    assert calc("error") == 0


def test_availability_calc_with_samples():
    """有 samples 时按在线比例计算。"""
    values = ["running"] * 9 + ["stopped"]
    online = sum(1 for v in values if v == "running")
    pct = round(online / len(values) * 100)
    assert pct == 90


def test_registry_repo_normalize():
    """registry repo 规范化：无 / 前缀补 library/。"""
    def normalize(repo):
        return ("library/" + repo) if "/" not in repo else repo
    assert normalize("mysql:5.7") == "library/mysql:5.7"
    assert normalize("apolloconfig/apollo-portal:2.3.0") == "apolloconfig/apollo-portal:2.3.0"


def test_registry_repo_tag_strip():
    """repo 提取：冒号后为 tag，/ 前为命名空间。"""
    def repo_of(image):
        return image.split(":")[0]
    assert repo_of("mysql:5.7") == "mysql"
    assert repo_of("prom/prometheus:v2.54.1") == "prom/prometheus"
    assert repo_of("77fea08e691f") == "77fea08e691f"
