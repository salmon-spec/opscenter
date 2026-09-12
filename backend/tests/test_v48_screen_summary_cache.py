"""任务 D 回归：/screen/summary 热缓存命中不再做 DB/指纹往返（热路径零 DB）。

改动前：每次请求（含 5s TTL 内命中）都做一次 DB 指纹往返，
热路径 0.25~0.55s 就来自这里，热 P95 0.895s 超标。
改动后：TTL 内命中直接返回内存载荷；TTL 过期始终重建，避免 DB 指纹未变化时
把 K3s/WG/日志等非 DB 数据无限续期。

红→绿：热命中不重建；TTL 过期必须刷新非 DB 数据；构建不持有缓存锁。
"""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app import topology
from app.main import app

client = TestClient(app)

STUB_BUSINESS = {
    "generated_at": "2026-09-12T00:00:00Z",
    "freshness": {"metrics_at": None, "services_at": None, "wireguard_at": None},
    "partial_errors": [],
    "source_status": {"k3s": "unavailable", "hosts": "ok", "services": "ok", "wireguard": "unknown"},
    "hosts_summary": {"total": 0, "online": 0, "offline": 0, "stale": 0},
    "containers_summary": {"running": 0, "stopped": 0, "unknown_hosts": 0},
    "databases_summary": {"total": 0, "connected": 0, "pending": 0, "error": 0},
    "services_summary": {"total": 0, "up": 0, "down": 0, "incidents": 0},
    "logs_summary": {"total": 0, "fresh": 0, "stale": 0, "abnormal": 0},
    "wireguard_summary": {"managed": 0, "healthy": 0, "warning": 0, "offline": 0, "unmanaged": 0},
    "alerts_summary": {"firing": 0, "acknowledged": 0},
    "k3s": None,
    "services_dual": None,
    "docker_hosts_count": 0,
    "elapsed_ms": 1,
    "servers": [],
    "services": [],
    "active_alerts": [],
    "trends": {"cpu": [], "memory": [], "net_rx": [], "net_tx": []},
}
RESPONSE_EXTRA_KEYS = {"data_timestamp", "cached", "cache_age_seconds"}


class _Probe:
    def __init__(self):
        self.build_calls = 0
        self.build_saw_lock_held = None


@pytest.fixture
def probe(monkeypatch):
    """全量构建换成计数器，不触碰真实 DB/K3s/WG。"""
    topology.reset_screen_cache()
    p = _Probe()

    def _build():
        p.build_calls += 1
        p.build_saw_lock_held = topology._SCREEN_CACHE_LOCK.locked()
        payload = deepcopy(STUB_BUSINESS)
        payload["k3s"] = {"generation": p.build_calls}
        return payload, f"2026-09-12T00:00:0{p.build_calls}Z"

    monkeypatch.setattr(topology, "_build_screen_business", _build)
    yield p
    topology.reset_screen_cache()


def _expire_cache(seconds=10):
    topology._SCREEN_CACHE["stored_at"] -= seconds


def test_hot_hit_does_not_rebuild(probe):
    r1 = client.get("/api/v2/screen/summary")
    assert r1.status_code == 200, r1.text
    assert r1.json()["cached"] is False
    assert probe.build_calls == 1
    assert probe.build_saw_lock_held is False  # 全量构建在锁外（不阻塞其它请求）

    r2 = client.get("/api/v2/screen/summary")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["cached"] is True
    assert body["cache_age_seconds"] >= 0.0
    # 热命中：零重建
    assert probe.build_calls == 1
    assert r2.headers["ETag"] == r1.headers["ETag"]


def test_expired_cache_refreshes_non_db_sources(probe):
    first = client.get("/api/v2/screen/summary")
    assert first.status_code == 200
    assert first.json()["k3s"] == {"generation": 1}
    _expire_cache(10)  # 模拟 TTL(5s) 已过期
    r = client.get("/api/v2/screen/summary")
    assert r.status_code == 200, r.text
    assert r.json()["cached"] is False
    assert r.json()["cache_age_seconds"] == 0.0
    assert r.json()["k3s"] == {"generation": 2}
    assert r.json()["data_timestamp"] != first.json()["data_timestamp"]
    assert probe.build_calls == 2


def test_etag_and_304_unchanged(probe):
    r1 = client.get("/api/v2/screen/summary")
    etag = r1.headers["ETag"]
    assert etag.startswith('"') and etag.endswith('"')

    r304 = client.get("/api/v2/screen/summary", headers={"If-None-Match": etag})
    assert r304.status_code == 304 and r304.content == b""
    assert r304.headers["ETag"] == etag
    assert client.get("/api/v2/screen/summary",
                      headers={"If-None-Match": f"W/{etag}"}).status_code == 304
    assert client.get("/api/v2/screen/summary",
                      headers={"If-None-Match": "*"}).status_code == 304
    # 全部 304 都是热命中，不重建
    assert probe.build_calls == 1


def test_response_field_set_unchanged(probe):
    body = client.get("/api/v2/screen/summary").json()
    assert set(body.keys()) == set(STUB_BUSINESS.keys()) | RESPONSE_EXTRA_KEYS
    assert body["data_timestamp"] == "2026-09-12T00:00:01Z"
    assert body["cache_age_seconds"] == 0.0
