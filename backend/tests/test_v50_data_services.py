"""v5.0.0 数据服务 API 路由测试（BE-5）。

覆盖：7 件中间件 overview 并行卡片、单件 status/probe、只读浏览各端点 data 形状、
降级（status() 抛异常 / 列表方法抛异常 → HTTP 200 + partial_errors）、未知 kind 404、
OPERATOR_TOKEN 鉴权、overview 探测超时。全部使用 Fake 单例，不连真实中间件。
"""

import time

import pytest
from fastapi.testclient import TestClient

from app import auth
from app import data_services
from app.main import app

client = TestClient(app)
BASE = "/api/v2/data-services"
KINDS = ["redis", "rabbitmq", "kafka", "zookeeper", "nacos", "minio", "mongodb"]
ENVELOPE_KEYS = {"data", "data_timestamp", "cached", "cache_age_seconds", "partial_errors", "source_status"}


class FakeService:
    """中间件 Fake：status() 可抛异常/返回标准状态；available() 跟随同一开关。"""

    def __init__(self, kind, *, fail=False, available=True, latency_ms=2.5,
                 error="", detail=None, configured=True):
        self.kind = kind
        self._fail = fail
        self._available = available
        self._latency_ms = latency_ms
        self._error = error
        self._detail = dict(detail or {})
        self._configured = configured

    def available(self):
        if self._fail:
            raise RuntimeError(f"{self.kind} boom")
        return self._available

    def status(self):
        if self._fail:
            raise RuntimeError(f"{self.kind} down")
        return {
            "kind": self.kind,
            "configured": self._configured,
            "available": self._available,
            "latency_ms": self._latency_ms,
            "error": self._error,
            "detail": dict(self._detail),
        }


def _build_fakes(*, fail=()):
    fail = set(fail)

    redis = FakeService("redis", fail="redis" in fail,
                        detail={"endpoint": "redis.local:6379/0", "version": "7.2.4"})
    redis.info = lambda section=None: {
        "used_memory": 1048576, "maxmemory": 268435456, "maxmemory_policy": "allkeys-lru",
        "keyspace": {"db0": {"keys": 3, "expires": 1, "avg_ttl": 0}},
    }
    redis.dbsize = lambda: 3
    redis.scan = lambda cursor=0, pattern="*", count=100: (7, ["key:a", "key:b"])
    redis.key_meta = lambda key: {"key": key, "type": "string", "ttl": -1}

    mq = FakeService("rabbitmq", fail="rabbitmq" in fail,
                     detail={"endpoint": "rabbitmq.local:5672", "queue": "opscenter.tasks",
                             "dlq": "opscenter.tasks.dlq", "worker_running": True, "leader": True})
    mq.queues = lambda: [{"name": "opscenter.tasks", "messages": 3, "messages_ready": 2,
                          "messages_unacknowledged": 1, "consumers": 1, "state": "running"}]

    kafka = FakeService("kafka", fail="kafka" in fail,
                        detail={"bootstrap": "kafka.local:9092", "topic": "opscenter.events"})
    kafka.topics = lambda limit=200: [{"name": "opscenter.events", "partitions": 3, "replicas": 1}]
    kafka.consumer_groups = lambda limit=100: [
        {"group": "opscenter", "state": "Stable", "members": 1, "topics": ["opscenter.events"]}]

    zk = FakeService("zookeeper", fail="zookeeper" in fail,
                     detail={"endpoints": "zk.local:2181", "state": "CONNECTED"})
    zk.tree = lambda path="/", depth=2, max_nodes=200: {
        "path": path, "children": [{"name": "kafka", "path": "/kafka", "has_children": True}],
        "truncated": False}

    nacos = FakeService("nacos", fail="nacos" in fail,
                        detail={"endpoint": "nacos.local:8848", "group": "DEFAULT_GROUP",
                                "data_id": "opscenter.dynamic.json"})
    nacos.list_services = lambda: [{"name": "opscenter", "group": "DEFAULT_GROUP",
                                    "instance_count": 1, "healthy_count": 1}]
    nacos.list_configs = lambda *, group=None, page=1, size=50: {
        "total": 1,
        "configs": [{"data_id": "opscenter.dynamic.json", "group": group or "DEFAULT_GROUP",
                     "updated_at": "2026-09-10T00:00:00Z"}]}

    minio = FakeService("minio", fail="minio" in fail,
                        detail={"endpoint": "minio.local:9000", "bucket": "opscenter", "secure": False})
    minio.list_buckets = lambda: [{"name": "opscenter", "creation_date": "2026-09-09T00:00:00Z"}]
    minio.list_objects = lambda *, prefix="", bucket=None, limit=100: [
        {"name": f"{prefix}x.txt", "size": 12, "last_modified": "2026-09-10T00:00:00Z"}]

    mongo = FakeService("mongodb", fail="mongodb" in fail,
                        detail={"endpoint": "mongo.local:27017", "db": "opscenter"})
    mongo.list_databases = lambda: [{"name": "opscenter", "size_on_disk": 1024,
                                     "collections": 5, "empty": False}]
    mongo.list_collections = lambda db=None, limit=200: [
        {"name": "audit_events", "count": 10, "size": 2048}]

    return {
        "redis_store": redis,
        "task_queue": mq,
        "event_bus": kafka,
        "zk_view": zk,
        "nacos_config": nacos,
        "object_store": minio,
        "doc_store": mongo,
    }


def _install(monkeypatch, *, fail=()):
    objs = _build_fakes(fail=fail)
    for attr, obj in objs.items():
        monkeypatch.setattr(data_services, attr, obj)
    return objs


@pytest.fixture(autouse=True)
def _auth_off(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "")


@pytest.fixture()
def fakes(monkeypatch):
    return _install(monkeypatch)


def _assert_envelope(body):
    assert set(body) == ENVELOPE_KEYS
    assert isinstance(body["data_timestamp"], int) and body["data_timestamp"] > 1_600_000_000
    assert body["cached"] is False
    assert body["cache_age_seconds"] == 0
    assert isinstance(body["partial_errors"], list)
    assert isinstance(body["source_status"], dict)


# ── 正常路径 ─────────────────────────────────────────────────


def test_overview_seven_available_cards(fakes):
    res = client.get(f"{BASE}/overview")
    assert res.status_code == 200
    body = res.json()
    _assert_envelope(body)
    assert body["partial_errors"] == []
    services = body["data"]["services"]
    assert [s["kind"] for s in services] == KINDS
    card_keys = {"kind", "name", "configured", "available", "latency_ms",
                 "endpoint", "version", "summary", "error"}
    for card in services:
        assert set(card) == card_keys
        assert card["available"] is True and card["configured"] is True
        assert card["error"] == ""
        assert isinstance(card["name"], str) and card["name"]
        assert isinstance(card["summary"], dict)
    by_kind = {s["kind"]: s for s in services}
    assert by_kind["redis"]["name"] == "Redis"
    assert by_kind["redis"]["endpoint"] == "redis.local:6379/0"
    assert by_kind["redis"]["version"] == "7.2.4"
    assert by_kind["kafka"]["endpoint"] == "kafka.local:9092"
    assert by_kind["zookeeper"]["endpoint"] == "zk.local:2181"
    assert by_kind["rabbitmq"]["summary"]["queue"] == "opscenter.tasks"
    assert by_kind["mongodb"]["summary"]["db"] == "opscenter"
    assert body["source_status"] == {kind: True for kind in KINDS}


def test_status_endpoint_returns_card(fakes):
    body = client.get(f"{BASE}/redis/status").json()
    _assert_envelope(body)
    card = body["data"]
    assert card["kind"] == "redis" and card["available"] is True
    assert card["endpoint"] == "redis.local:6379/0" and card["version"] == "7.2.4"
    assert body["source_status"] == {"redis": True}
    assert body["partial_errors"] == []


def test_probe_forces_refresh_and_returns_result(fakes):
    calls = []
    orig_status = fakes["redis_store"].status

    def counting_status():
        calls.append(1)
        return orig_status()

    fakes["redis_store"].status = counting_status
    fakes["redis_store"]._status_cache = {"stale": True}
    body = client.post(f"{BASE}/redis/probe").json()
    _assert_envelope(body)
    assert body["data"] == {"kind": "redis", "available": True, "latency_ms": 2.5, "error": ""}
    assert calls == [1]
    assert fakes["redis_store"]._status_cache is None
    assert body["source_status"] == {"redis": True}


def test_redis_keyspace_shape(fakes):
    body = client.get(f"{BASE}/redis/keyspace").json()
    _assert_envelope(body)
    data = body["data"]
    assert data["dbsize"] == 3
    assert data["keyspace"] == {"db0": {"keys": 3, "expires": 1, "avg_ttl": 0}}
    assert data["memory"] == {"used": 1048576, "max": 268435456, "policy": "allkeys-lru"}
    assert data["available"] is True
    assert body["source_status"] == {"redis": True}


def test_redis_keyspace_memory_key_compat(fakes):
    fakes["redis_store"].info = lambda section=None: {
        "memory_used": "2048", "memory_max": "4096", "memory_policy": "noeviction"}
    data = client.get(f"{BASE}/redis/keyspace").json()["data"]
    assert data["memory"] == {"used": 2048, "max": 4096, "policy": "noeviction"}
    assert data["keyspace"] == {}


def test_redis_keys_shape_and_count_cap(fakes):
    captured = {}

    def fake_scan(cursor=0, pattern="*", count=100):
        captured.update(cursor=cursor, pattern=pattern, count=count)
        return 11, ["a", "b"]

    fakes["redis_store"].scan = fake_scan
    fakes["redis_store"].key_meta = lambda key: {"key": key, "type": "hash", "ttl": 42}
    body = client.get(f"{BASE}/redis/keys",
                      params={"pattern": "ops:*", "cursor": 5, "count": 999}).json()
    _assert_envelope(body)
    assert captured == {"cursor": 5, "pattern": "ops:*", "count": 200}
    assert body["data"]["cursor"] == 11
    assert body["data"]["keys"] == [
        {"key": "a", "type": "hash", "ttl": 42},
        {"key": "b", "type": "hash", "ttl": 42},
    ]
    assert body["data"]["available"] is True


def test_middleware_list_endpoints_shape(fakes):
    rb = client.get(f"{BASE}/rabbitmq/queues").json()
    assert rb["data"]["queues"][0]["name"] == "opscenter.tasks"
    assert rb["data"]["queues"][0]["messages"] == 3

    kt = client.get(f"{BASE}/kafka/topics").json()
    assert kt["data"]["topics"][0]["partitions"] == 3
    kg = client.get(f"{BASE}/kafka/consumer-groups").json()
    assert kg["data"]["groups"][0]["group"] == "opscenter"

    zk = client.get(f"{BASE}/zookeeper/tree", params={"path": "/kafka", "depth": 3}).json()
    assert zk["data"]["path"] == "/kafka"
    assert zk["data"]["children"][0]["name"] == "kafka"
    assert zk["data"]["truncated"] is False

    ns = client.get(f"{BASE}/nacos/services").json()
    assert ns["data"]["services"][0]["instance_count"] == 1
    nc = client.get(f"{BASE}/nacos/configs", params={"group": "G1", "page": 2, "size": 10}).json()
    assert nc["data"]["total"] == 1
    assert nc["data"]["configs"][0]["group"] == "G1"

    mb = client.get(f"{BASE}/minio/buckets").json()
    assert mb["data"]["buckets"][0]["name"] == "opscenter"
    mo = client.get(f"{BASE}/minio/objects",
                    params={"bucket": "opscenter", "prefix": "reports/", "limit": 5}).json()
    assert mo["data"]["objects"][0]["name"] == "reports/x.txt"

    md = client.get(f"{BASE}/mongodb/databases").json()
    assert md["data"]["databases"][0]["name"] == "opscenter"
    mc = client.get(f"{BASE}/mongodb/collections",
                    params={"db": "opscenter", "limit": 50}).json()
    assert mc["data"]["collections"][0]["name"] == "audit_events"


def test_list_endpoint_default_params_passthrough(fakes):
    captured = {}
    fakes["nacos_config"].list_configs = (
        lambda *, group=None, page=1, size=50: captured.update(nacos=(group, page, size))
        or {"total": 0, "configs": []})
    fakes["object_store"].list_objects = (
        lambda *, prefix="", bucket=None, limit=100: captured.update(minio=(prefix, bucket, limit))
        or [])
    fakes["doc_store"].list_collections = (
        lambda db=None, limit=200: captured.update(mongo=(db, limit)) or [])

    client.get(f"{BASE}/nacos/configs")
    client.get(f"{BASE}/minio/objects")
    client.get(f"{BASE}/mongodb/collections")
    assert captured["nacos"] == (None, 1, 50)
    assert captured["minio"] == ("", None, 100)
    assert captured["mongo"] == (None, 100)


# ── 降级路径（HTTP 200 + available=false + partial_errors） ──


def test_overview_degraded_card(monkeypatch):
    _install(monkeypatch, fail={"redis"})
    res = client.get(f"{BASE}/overview")
    assert res.status_code == 200
    body = res.json()
    by_kind = {s["kind"]: s for s in body["data"]["services"]}
    assert by_kind["redis"]["available"] is False
    assert by_kind["redis"]["error"] == "redis down"
    assert body["partial_errors"] == ["redis: redis down"]
    assert body["source_status"]["redis"] is False
    assert all(by_kind[k]["available"] is True for k in KINDS if k != "redis")


def test_status_degraded(monkeypatch):
    _install(monkeypatch, fail={"kafka"})
    res = client.get(f"{BASE}/kafka/status")
    assert res.status_code == 200
    body = res.json()
    assert body["data"]["available"] is False
    assert body["data"]["error"] == "kafka down"
    assert body["partial_errors"] == ["kafka: kafka down"]
    assert body["source_status"] == {"kafka": False}


def test_probe_degraded(monkeypatch):
    _install(monkeypatch, fail={"nacos"})
    res = client.post(f"{BASE}/nacos/probe")
    assert res.status_code == 200
    body = res.json()
    assert body["data"]["kind"] == "nacos"
    assert body["data"]["available"] is False
    assert body["data"]["error"] == "nacos down"
    assert body["data"]["latency_ms"] is not None
    assert body["partial_errors"] == ["nacos: nacos down"]


def test_list_endpoint_degraded(monkeypatch):
    objs = _install(monkeypatch)

    def boom():
        raise RuntimeError("mgmt api down")

    objs["task_queue"].queues = boom
    res = client.get(f"{BASE}/rabbitmq/queues")
    assert res.status_code == 200
    body = res.json()
    assert body["data"]["queues"] == []
    assert body["data"]["available"] is False
    assert body["partial_errors"] == ["rabbitmq: mgmt api down"]
    assert body["source_status"] == {"rabbitmq": False}


def test_redis_keys_degraded(monkeypatch):
    objs = _install(monkeypatch)

    def boom(cursor=0, pattern="*", count=100):
        raise RuntimeError("redis scan down")

    objs["redis_store"].scan = boom
    res = client.get(f"{BASE}/redis/keys")
    assert res.status_code == 200
    body = res.json()
    assert body["data"] == {"cursor": 0, "keys": [], "available": False}
    assert body["partial_errors"] == ["redis: redis scan down"]


def test_overview_probe_timeout(monkeypatch):
    objs = _install(monkeypatch)

    def hang():
        time.sleep(0.5)
        return {"kind": "kafka", "configured": True, "available": True,
                "latency_ms": 1.0, "error": "", "detail": {}}

    objs["event_bus"].status = hang
    monkeypatch.setattr(data_services, "_OVERVIEW_TIMEOUT", 0.15)
    started = time.monotonic()
    res = client.get(f"{BASE}/overview")
    elapsed = time.monotonic() - started
    assert res.status_code == 200
    body = res.json()
    card = {s["kind"]: s for s in body["data"]["services"]}["kafka"]
    assert card["available"] is False
    assert card["error"] == "probe timeout"
    assert "kafka: probe timeout" in body["partial_errors"]
    assert body["source_status"]["kafka"] is False
    assert elapsed < 2.0


# ── 404 / 鉴权 ───────────────────────────────────────────────


def test_unknown_kind_404(fakes):
    assert client.get(f"{BASE}/clickhouse/status").status_code == 404
    assert client.post(f"{BASE}/clickhouse/probe").status_code == 404


def test_operator_token_auth(monkeypatch, fakes):
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "secret")
    assert client.get(f"{BASE}/overview").status_code == 401
    assert client.get(f"{BASE}/overview",
                      headers={"Authorization": "Bearer wrong"}).status_code == 401
    res = client.get(f"{BASE}/overview", headers={"Authorization": "Bearer secret"})
    assert res.status_code == 200
    assert len(res.json()["data"]["services"]) == 7
