"""v5.0.0 中间件客户端单元测试（BE-3）：MongoDB doc_store + Kafka event_bus。

不连真实服务：monkeypatch 模块级 `_client` / `_producer` / `_admin` 为 Fake 对象，
Fake 只实现被测代码用到的 pymongo / kafka-python API 形态。

运行（backend 目录）：
  New-Item -ItemType Directory -Force -Path .tmp
  $env:DATABASE_URL="sqlite:///./.tmp/test-v50-doc.db"
  python -m pytest tests/test_v50_doc_event.py -v
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app import config
from app.services import doc_store as ds
from app.services import event_bus as eb

MONGO_URL = "mongodb://opsuser:s3cret@mongo.test:27017/?authSource=admin"
BOOTSTRAP = "kafka.test:9092"
TOPIC = "opscenter.events"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """每个用例独立的模块级状态与配置默认值。"""
    monkeypatch.setattr(ds, "_client", None)
    monkeypatch.setattr(ds, "_AVAIL", {"at": 0.0, "ok": False, "latency": None, "error": ""})
    monkeypatch.setattr(eb, "_producer", None)
    monkeypatch.setattr(eb, "_admin", None)
    monkeypatch.setattr(eb, "_CACHE", {"at": 0.0, "ok": False, "latency": None, "error": ""})
    monkeypatch.setattr(config, "MONGO_URL", "")
    monkeypatch.setattr(config, "MONGO_DB", "opscenter")
    monkeypatch.setattr(config, "MONGO_ENABLED", False)
    monkeypatch.setattr(config, "KAFKA_BOOTSTRAP", "")
    monkeypatch.setattr(config, "KAFKA_TOPIC", TOPIC)
    monkeypatch.setattr(config, "KAFKA_ENABLED", False)
    monkeypatch.setattr(config, "MW_PROBE_TIMEOUT", 2.0)
    yield


# ────────────────────────────── MongoDB Fakes ──────────────────────────────

class FakeMongoCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self.sorts = []
        self.limit_value = None

    def sort(self, key, direction):
        self.sorts.append((key, direction))
        reverse = direction == -1

        def sort_key(doc):
            value = doc.get(key)
            return value if value is not None else 0

        self._docs.sort(key=sort_key, reverse=reverse)
        return self

    def limit(self, value):
        self.limit_value = value
        self._docs = self._docs[:value]
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeMongoCollection:
    def __init__(self, name):
        self.name = name
        self.docs = []
        self.inserted = []
        self.fail = False
        self.last_cursor = None

    def insert_one(self, doc):
        if self.fail:
            raise RuntimeError("insert failed")
        self.inserted.append(dict(doc))
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=len(self.docs))

    def find(self, filters=None):
        if self.fail:
            raise RuntimeError("find failed")
        docs = [doc for doc in self.docs
                if all(doc.get(key) == value for key, value in (filters or {}).items())]
        self.last_cursor = FakeMongoCursor(docs)
        return self.last_cursor

    def estimated_document_count(self):
        if self.fail:
            raise RuntimeError("count failed")
        return len(self.docs)


class FakeMongoDatabase:
    def __init__(self, name):
        self.name = name
        self.collections = {}
        self.names_fail = False
        self.collstats_fail = False
        self.collstats_size = 4096

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeMongoCollection(name))

    def list_collection_names(self):
        if self.names_fail:
            raise RuntimeError("list_collection_names failed")
        return list(self.collections)

    def command(self, name, *args, **kwargs):
        if name != "collstats":
            raise RuntimeError(f"unsupported command {name}")
        if self.collstats_fail:
            raise RuntimeError("collstats not authorized")
        collection = self.collections[args[0]]
        return {"count": len(collection.docs), "size": self.collstats_size}


class FakeAdminDatabase:
    def __init__(self, client):
        self.client = client
        self.ping_calls = 0

    def command(self, name, *args, **kwargs):
        if name == "ping":
            if self.client.ping_fail:
                raise RuntimeError(self.client.ping_error)
            self.ping_calls += 1
            return {"ok": 1}
        if name == "listDatabases":
            if self.client.list_databases_fail:
                raise RuntimeError("listDatabases not authorized")
            return self.client.list_databases_result
        raise RuntimeError(f"unsupported command {name}")


class FakeMongoClient:
    def __init__(self, databases=None, list_databases_result=None):
        self.databases = {name: FakeMongoDatabase(name) for name in (databases or [])}
        self.list_databases_result = list_databases_result or {"databases": []}
        self.list_databases_fail = False
        self.list_names_fail = False
        self.ping_fail = False
        self.ping_error = "server selection failed"
        self.admin = FakeAdminDatabase(self)
        self.closed = False

    def __getitem__(self, name):
        return self.databases.setdefault(name, FakeMongoDatabase(name))

    def list_database_names(self):
        if self.list_names_fail:
            raise RuntimeError("list_database_names failed")
        return list(self.databases)

    def close(self):
        self.closed = True


def _configured_mongo(monkeypatch, client):
    monkeypatch.setattr(config, "MONGO_URL", MONGO_URL)
    monkeypatch.setattr(config, "MONGO_ENABLED", True)
    monkeypatch.setattr(ds, "_client", client)


def _seed(client, docs, collection="audit_events", db="opscenter"):
    target = client[db][collection]
    target.docs = [dict(doc) for doc in docs]
    return target


# ────────────────────────────── doc_store 测试 ──────────────────────────────

def test_doc_store_unconfigured_fail_open(monkeypatch):
    store = ds.doc_store
    status = store.status()
    assert status["kind"] == "mongodb"
    assert status["configured"] is False
    assert status["available"] is False
    assert status["latency_ms"] is None
    assert status["detail"] == {"endpoint": "", "db": "opscenter"}
    assert store.available() is False
    assert store.insert("audit_events", {"x": 1}) is None
    assert store.query("audit_events") == []
    assert store.count("audit_events") == 0
    assert store.list_databases() == []
    assert store.list_collections() == []


def test_doc_store_insert_gates_whitelist_and_enabled(monkeypatch):
    client = FakeMongoClient()
    store = ds.doc_store

    # 未配置 MONGO_URL：即使 MONGO_ENABLED=true 也不写
    monkeypatch.setattr(config, "MONGO_ENABLED", True)
    assert store.insert("audit_events", {"x": 1}) is None

    _configured_mongo(monkeypatch, client)
    # 白名单外：直接忽略，不触碰数据库
    assert store.insert("evil_collection", {"x": 1}) is None
    assert client.databases == {}

    # MONGO_ENABLED=false：白名单也不写
    monkeypatch.setattr(config, "MONGO_ENABLED", False)
    assert store.insert("audit_events", {"x": 1}) is None
    assert client.databases == {}

    # 门禁全开：写入成功
    monkeypatch.setattr(config, "MONGO_ENABLED", True)
    inserted_id = store.insert("audit_events", {"x": 1})
    assert isinstance(inserted_id, str)
    inserted = client["opscenter"]["audit_events"].inserted
    assert len(inserted) == 1
    assert inserted[0]["x"] == 1
    assert isinstance(inserted[0]["ts"], int)


def test_doc_store_insert_fills_ts_and_preserves_explicit(monkeypatch):
    client = FakeMongoClient()
    _configured_mongo(monkeypatch, client)
    store = ds.doc_store

    store.insert("audit_events", {"action": "create"})
    stored = client["opscenter"]["audit_events"].inserted[0]
    assert isinstance(stored["ts"], int)
    assert abs(stored["ts"] - int(time.time())) < 5

    store.insert("audit_events", {"action": "update", "ts": 123})
    assert client["opscenter"]["audit_events"].inserted[1]["ts"] == 123


def test_doc_store_query_sort_limit_id_and_filters(monkeypatch):
    client = FakeMongoClient()
    _configured_mongo(monkeypatch, client)
    collection = _seed(client, [
        {"_id": 1, "ts": 10, "resource": "host"},
        {"_id": 2, "ts": 20, "resource": "server"},
        {"_id": 3, "ts": 30, "resource": "host"},
        {"_id": 4, "ts": 40, "resource": "host"},
        {"_id": 5, "ts": 50, "resource": "server"},
    ])
    store = ds.doc_store

    rows = store.query("audit_events", limit=3)
    assert [row["ts"] for row in rows] == [50, 40, 30]
    assert [row["_id"] for row in rows] == ["5", "4", "3"]
    assert collection.last_cursor.sorts == [("ts", -1)]

    rows = store.query("audit_events", limit=2, sort_desc=False)
    assert [row["ts"] for row in rows] == [10, 20]
    assert collection.last_cursor.sorts == [("ts", 1)]

    rows = store.query("audit_events", filters={"resource": "server"})
    assert [row["_id"] for row in rows] == ["5", "2"]

    assert store.query("not_whitelisted") == []


def test_doc_store_query_limit_capped_at_500(monkeypatch):
    client = FakeMongoClient()
    _configured_mongo(monkeypatch, client)
    collection = _seed(client, [{"_id": i, "ts": i} for i in range(520)], collection="reports")
    store = ds.doc_store

    rows = store.query("reports", limit=9999)
    assert len(rows) == 500
    assert collection.last_cursor.limit_value == 500


def test_doc_store_query_ignores_mongo_enabled_and_fails_open(monkeypatch):
    client = FakeMongoClient()
    _configured_mongo(monkeypatch, client)
    collection = _seed(client, [{"_id": 1, "ts": 10}])
    store = ds.doc_store

    # 只读浏览不受 MONGO_ENABLED 限制
    monkeypatch.setattr(config, "MONGO_ENABLED", False)
    assert len(store.query("audit_events")) == 1
    assert store.count("audit_events") == 1

    # 底层异常 → 空结果，不抛
    collection.fail = True
    assert store.query("audit_events") == []
    assert store.count("audit_events") == 0


def test_doc_store_count(monkeypatch):
    client = FakeMongoClient()
    _configured_mongo(monkeypatch, client)
    _seed(client, [{"_id": 1}, {"_id": 2}])
    store = ds.doc_store

    assert store.count("audit_events") == 2
    assert store.count("not_whitelisted") == 0


def test_doc_store_list_databases_success_and_degrade(monkeypatch):
    client = FakeMongoClient(databases=["opscenter", "local"])
    client.list_databases_result = {"databases": [
        {"name": "opscenter", "sizeOnDisk": 2048, "empty": False, "collections": 5},
        {"name": "local", "sizeOnDisk": 0, "empty": True},
    ]}
    _configured_mongo(monkeypatch, client)
    store = ds.doc_store

    assert store.list_databases() == [
        {"name": "opscenter", "size_on_disk": 2048, "collections": 5, "empty": False},
        {"name": "local", "size_on_disk": 0, "collections": 0, "empty": True},
    ]

    # listDatabases 权限不足 → list_database_names 降级（sizes=0）
    client.list_databases_fail = True
    degraded = store.list_databases()
    assert [row["name"] for row in degraded] == ["opscenter", "local"]
    assert all(row["size_on_disk"] == 0 for row in degraded)

    # 两级都失败 → []
    client.list_names_fail = True
    assert store.list_databases() == []


def test_doc_store_list_collections_success_and_degrade(monkeypatch):
    client = FakeMongoClient()
    _configured_mongo(monkeypatch, client)
    database = client["opscenter"]
    _seed(client, [{}, {}], collection="audit_events")
    _seed(client, [{}], collection="reports")
    store = ds.doc_store

    assert store.list_collections() == [
        {"name": "audit_events", "count": 2, "size": 4096},
        {"name": "reports", "count": 1, "size": 4096},
    ]

    # collstats 失败 → count/size 记 0，集合名仍返回
    database.collstats_fail = True
    assert store.list_collections() == [
        {"name": "audit_events", "count": 0, "size": 0},
        {"name": "reports", "count": 0, "size": 0},
    ]

    # 集合列表失败 → []
    database.names_fail = True
    assert store.list_collections() == []


def test_doc_store_status_and_available_cache(monkeypatch):
    client = FakeMongoClient()
    client.ping_error = "Authentication failed: mongodb://opsuser:s3cret@mongo.test:27017"
    _configured_mongo(monkeypatch, client)
    store = ds.doc_store

    status = store.status()
    assert status["kind"] == "mongodb"
    assert status["configured"] is True
    assert status["available"] is True
    assert isinstance(status["latency_ms"], float)
    assert status["error"] == ""
    assert status["detail"] == {"endpoint": "mongo.test:27017", "db": "opscenter"}

    # available() 10s 缓存：探测失败後仍返回缓存值
    assert store.available() is True
    client.ping_fail = True
    assert store.available() is True

    # 缓存过期 → 重新探测
    ds._AVAIL["at"] = 0.0
    assert store.available() is False

    # status() 实时探测且错误脱敏（不回显账号密码）
    status = store.status()
    assert status["available"] is False
    assert status["error"]
    assert "s3cret" not in status["error"]
    assert "opsuser" not in status["error"]


def test_doc_store_detail_db_from_url_path(monkeypatch):
    client = FakeMongoClient()
    monkeypatch.setattr(config, "MONGO_URL",
                        "mongodb://u:p@mongo.test:27017/auditdb?authSource=admin")
    monkeypatch.setattr(ds, "_client", client)
    status = ds.doc_store.status()
    assert status["detail"] == {"endpoint": "mongo.test:27017", "db": "auditdb"}


# ─────────────────────────────── Kafka Fakes ───────────────────────────────

class FakeFuture:
    def __init__(self, error=None):
        self.error = error
        self.timeout = None

    def get(self, timeout=None):
        self.timeout = timeout
        if self.error:
            raise self.error
        return SimpleNamespace(topic=TOPIC, partition=0, offset=1)


class FakeProducer:
    def __init__(self, error=None):
        self.error = error
        self.sent = []
        self.futures = []
        self.closed = False

    def send(self, topic, key=None, value=None, **kwargs):
        future = FakeFuture(error=self.error)
        self.futures.append(future)
        self.sent.append({"topic": topic, "key": key, "value": value, "kwargs": kwargs})
        return future

    def close(self):
        self.closed = True


class FakeAdmin:
    def __init__(self):
        self.topics_meta = None
        self.groups = []
        self.descriptions = []
        self.list_topics_calls = []
        self.fail_topics = False
        self.fail_groups = False
        self.fail_describe = False
        self.closed = False

    def list_topics(self, timeout=None):
        self.list_topics_calls.append(timeout)
        if self.fail_topics:
            raise RuntimeError("kafka unreachable")
        return self.topics_meta

    def describe_topics(self, timeout=None):
        return self.topics_meta

    def list_consumer_groups(self, timeout=None):
        if self.fail_groups:
            raise RuntimeError("groups unavailable")
        return self.groups

    def describe_consumer_groups(self, group_ids, timeout=None):
        if self.fail_describe:
            raise RuntimeError("describe unavailable")
        wanted = set(group_ids)
        return [item for item in self.descriptions
                if getattr(item, "group_id", None) in wanted]

    def close(self):
        self.closed = True


def _configured_kafka(monkeypatch, admin=None, producer=None):
    monkeypatch.setattr(config, "KAFKA_BOOTSTRAP", BOOTSTRAP)
    monkeypatch.setattr(config, "KAFKA_ENABLED", True)
    if admin is not None:
        monkeypatch.setattr(eb, "_admin", admin)
    if producer is not None:
        monkeypatch.setattr(eb, "_producer", producer)


# ────────────────────────────── event_bus 测试 ──────────────────────────────

def test_event_bus_unconfigured_publish_false(monkeypatch):
    producer = FakeProducer()
    monkeypatch.setattr(eb, "_producer", producer)
    bus = eb.event_bus

    # BOOTSTRAP 为空
    monkeypatch.setattr(config, "KAFKA_ENABLED", True)
    assert bus.publish("audit", {"a": 1}) is False

    # KAFKA_ENABLED=false
    monkeypatch.setattr(config, "KAFKA_BOOTSTRAP", BOOTSTRAP)
    monkeypatch.setattr(config, "KAFKA_ENABLED", False)
    assert bus.publish("audit", {"a": 1}) is False

    assert producer.sent == []


def test_event_bus_publish_ok(monkeypatch):
    producer = FakeProducer()
    _configured_kafka(monkeypatch, producer=producer)
    payload = {"action": "create"}
    bus = eb.event_bus

    assert bus.publish("audit", payload) is True
    sent = producer.sent[0]
    assert sent["topic"] == TOPIC
    assert sent["key"] == b"audit"
    assert sent["value"]["event_type"] == "audit"
    assert isinstance(sent["value"]["ts"], int)
    assert producer.futures[0].timeout == config.MW_PROBE_TIMEOUT
    # payload 拷贝，不污染调用方原始 dict
    assert payload == {"action": "create"}

    # 自带 ts 保留
    bus.publish("audit", {"ts": 123})
    assert producer.sent[1]["value"]["ts"] == 123


def test_event_bus_publish_failure_returns_false(monkeypatch):
    producer = FakeProducer(error=RuntimeError("broker down with sasl user:pass"))
    _configured_kafka(monkeypatch, producer=producer)
    assert eb.event_bus.publish("audit", {"a": 1}) is False


def test_event_bus_topics_cluster_metadata(monkeypatch):
    admin = FakeAdmin()
    admin.topics_meta = SimpleNamespace(topics={
        "z-topic": SimpleNamespace(partitions={
            0: SimpleNamespace(replicas=[1, 2, 3]),
            1: SimpleNamespace(replicas=[1, 2, 3]),
        }),
        "a-topic": SimpleNamespace(partitions={0: SimpleNamespace(replicas=[1])}),
    })
    _configured_kafka(monkeypatch, admin=admin)
    bus = eb.event_bus

    assert bus.topics() == [
        {"name": "a-topic", "partitions": 1, "replicas": 1},
        {"name": "z-topic", "partitions": 2, "replicas": 3},
    ]
    assert admin.list_topics_calls == [config.MW_PROBE_TIMEOUT]
    assert [item["name"] for item in bus.topics(limit=1)] == ["a-topic"]


def test_event_bus_topics_real_kafka_python_shape(monkeypatch):
    class RealShapeAdmin:
        def __init__(self):
            self.described = False

        def list_topics(self):
            return ["t1", "t2"]

        def describe_topics(self):
            self.described = True
            return [
                {"topic": "t1", "partitions": [{"partition": 0, "replicas": [1, 2]}]},
                {"topic": "t2", "partitions": [{"partition": 0, "replicas": [1]},
                                               {"partition": 1, "replicas": [1]}]},
            ]

    admin = RealShapeAdmin()
    _configured_kafka(monkeypatch, admin=admin)
    rows = eb.event_bus.topics()
    assert admin.described is True
    assert rows == [
        {"name": "t1", "partitions": 1, "replicas": 2},
        {"name": "t2", "partitions": 2, "replicas": 1},
    ]


def test_event_bus_topics_fail_open(monkeypatch):
    admin = FakeAdmin()
    admin.fail_topics = True
    _configured_kafka(monkeypatch, admin=admin)
    assert eb.event_bus.topics() == []

    monkeypatch.setattr(config, "KAFKA_BOOTSTRAP", "")
    monkeypatch.setattr(eb, "_admin", None)
    assert eb.event_bus.topics() == []


def test_event_bus_consumer_groups_merge(monkeypatch):
    admin = FakeAdmin()
    admin.groups = [("g1", "consumer"), ("g2", "consumer")]
    admin.descriptions = [
        SimpleNamespace(group_id="g1", state="Stable", members=[
            SimpleNamespace(member_assignment=SimpleNamespace(topics=["t1", "t2"])),
            SimpleNamespace(member_assignment=SimpleNamespace(topics=["t2"])),
        ]),
        SimpleNamespace(group_id="g2", state="Empty", members=[]),
    ]
    _configured_kafka(monkeypatch, admin=admin)
    bus = eb.event_bus

    assert bus.consumer_groups() == [
        {"group": "g1", "state": "Stable", "members": 2, "topics": ["t1", "t2"]},
        {"group": "g2", "state": "Empty", "members": 0, "topics": []},
    ]

    # describe 失败 → 保留 group 基础字段，不抛
    admin.fail_describe = True
    assert bus.consumer_groups() == [
        {"group": "g1", "state": "", "members": 0, "topics": []},
        {"group": "g2", "state": "", "members": 0, "topics": []},
    ]

    # list 失败 → []
    admin.fail_groups = True
    assert bus.consumer_groups() == []


def test_event_bus_status_and_available_cache(monkeypatch):
    bus = eb.event_bus

    status = bus.status()
    assert status["kind"] == "kafka"
    assert status["configured"] is False
    assert status["available"] is False
    assert status["detail"] == {"bootstrap": "", "topic": TOPIC}
    assert bus.available() is False

    admin = FakeAdmin()
    _configured_kafka(monkeypatch, admin=admin)
    status = bus.status()
    assert status["configured"] is True
    assert status["available"] is True
    assert isinstance(status["latency_ms"], float)
    assert status["error"] == ""
    assert status["detail"] == {"bootstrap": BOOTSTRAP, "topic": TOPIC}
    assert admin.list_topics_calls[-1] == config.MW_PROBE_TIMEOUT

    # available() 10s 缓存：探测失败后仍返回缓存值
    assert bus.available() is True
    admin.fail_topics = True
    assert bus.available() is True

    # 缓存过期 → 重新探测 False
    eb._CACHE["at"] = 0.0
    assert bus.available() is False

    # status() 实时探测
    status = bus.status()
    assert status["available"] is False
    assert status["error"]


def test_event_bus_close_never_raises(monkeypatch):
    producer, admin = FakeProducer(), FakeAdmin()
    monkeypatch.setattr(eb, "_producer", producer)
    monkeypatch.setattr(eb, "_admin", admin)

    eb.event_bus.close()
    assert producer.closed is True
    assert admin.closed is True
    assert eb._producer is None
    assert eb._admin is None

    class BoomClient:
        def close(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(eb, "_producer", BoomClient())
    monkeypatch.setattr(eb, "_admin", BoomClient())
    eb.event_bus.close()  # 绝不抛
    assert eb._producer is None
    assert eb._admin is None
