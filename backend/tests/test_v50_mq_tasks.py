"""v5.0.0 MQ 任务队列与统一任务注册表（BE-2）单元测试。

不连真实 RabbitMQ/Redis：
- FakeRedis 实现 tasks.py 用到的 available/get/set/get_json/set_json/delete 子集，
  通过 monkeypatch tasks.redis_store（模块属性）注入；
- FakeChannel/FakeConnection 实现 pika 阻塞连接形态，通过 monkeypatch
  mq._open_connection / MQ_URL / MQ_ENABLED 注入。

运行（在 backend 目录）：
  $env:DATABASE_URL='sqlite:///./.tmp/test-v50-mq.db'
  python -m pytest tests/test_v50_mq_tasks.py -v
"""

import json
import time

import pytest

from app.agent_tasks import AGENT_TASKS
from app.services import mq as mq_mod
from app.services import tasks as tasks_mod
from app.services.mq import task_queue


class FakeRedis:
    """tasks.py 所需 Redis 子集（JSON 存取 + TTL 记录 + 可用性）。"""

    def __init__(self):
        self.store = {}
        self.expires = {}

    def available(self):
        return True

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ttl=None):
        self.store[key] = value
        self.expires[key] = ttl
        return True

    def get_json(self, key):
        value = self.store.get(key)
        if value is None:
            return None
        return json.loads(json.dumps(value, ensure_ascii=False))

    def set_json(self, key, value, ttl=None):
        self.store[key] = json.loads(json.dumps(value, ensure_ascii=False))
        self.expires[key] = ttl
        return True

    def delete(self, *keys):
        removed = 0
        for key in keys:
            if self.store.pop(key, None) is not None:
                removed += 1
        return removed


class NoRedis:
    """模拟 Redis 不可用：available()=False，任何读写都应被短路。"""

    def __init__(self):
        self.calls = []

    def available(self):
        return False

    def get_json(self, key):
        self.calls.append(("get_json", key))
        return None

    def set_json(self, key, value, ttl=None):
        self.calls.append(("set_json", key))
        return False

    def delete(self, *keys):
        self.calls.append(("delete", keys))
        return 0


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """每个用例重置单例状态与模块配置，默认注入可用 FakeRedis。"""
    task_queue.stop_worker(timeout=1.0)
    fake = FakeRedis()
    monkeypatch.setattr(tasks_mod, "redis_store", fake)
    tasks_mod.task_registry._tasks.clear()

    monkeypatch.setattr(mq_mod, "MQ_ENABLED", False)
    monkeypatch.setattr(mq_mod, "MQ_URL", "")
    monkeypatch.setattr(mq_mod, "MQ_QUEUE", "opscenter.test.tasks")
    monkeypatch.setattr(mq_mod, "MQ_DLQ", "opscenter.test.tasks.dlq")
    monkeypatch.setattr(mq_mod, "MQ_MANAGEMENT_URL", "")
    monkeypatch.setattr(mq_mod, "MW_LEADER_ENABLED", False)
    monkeypatch.setattr(mq_mod, "MW_PROBE_TIMEOUT", 0.5)
    task_queue._handlers.clear()
    task_queue._reset_transport()
    task_queue._stop_event.clear()
    task_queue._worker = None
    task_queue._worker_running = False
    task_queue._leader = False
    task_queue._last_ok = False
    task_queue._last_error = ""
    yield fake


# ── a) 无 Redis：纯内存语义 ──────────────────────────────────────────────────

def test_tasks_memory_only_semantics(monkeypatch):
    no_redis = NoRedis()
    monkeypatch.setattr(tasks_mod, "redis_store", no_redis)
    registry = tasks_mod.task_registry

    created = registry.create("t-1", "demo", server_id="s-1", meta={"n": 1})
    assert created["status"] == "queued"
    assert created["phase"] == "排队中"
    assert created["meta"] == {"n": 1}
    assert created["message"] == "" and created["error"] == ""
    assert created["created_at"] <= created["updated_at"]
    assert registry.get("t-1") == created
    assert registry.get("missing") is None

    updated = registry.update("t-1", status="running", message="Bearer abc")
    assert updated["status"] == "running"
    assert updated["phase"] == "执行中"
    assert updated["message"] == "***abc"
    assert registry.update("missing", phase="x") is None

    finished = registry.finish("t-1", success=False, message="m", error="agent_token=xyz")
    assert finished["status"] == "failed"
    assert finished["phase"] == "失败"
    assert finished["error"] == "***=xyz"

    assert registry.get_by_server("s-1")["task_id"] == "t-1"
    assert registry.get_by_server("s-1", task_type="demo")["task_id"] == "t-1"
    assert registry.get_by_server("s-1", task_type="other") is None
    assert registry.get_by_server("missing") is None
    assert [item["task_id"] for item in registry.list()] == ["t-1"]
    assert registry.list(task_type="demo")[0]["task_id"] == "t-1"
    assert registry.list(task_type="other") == []

    assert registry.cleanup(86400) == 0
    assert registry.cleanup(0) == 1
    assert registry.get("t-1") is None
    assert no_redis.calls == []


def test_tasks_list_order_filter_and_limit(monkeypatch):
    monkeypatch.setattr(tasks_mod, "redis_store", NoRedis())
    registry = tasks_mod.task_registry
    first = registry.create("t-a", "demo", server_id="s-1")
    time.sleep(0.01)
    second = registry.create("t-b", "demo", server_id="s-2")
    assert [item["task_id"] for item in registry.list()] == [second["task_id"], first["task_id"]]
    assert [item["task_id"] for item in registry.list(server_id="s-1")] == ["t-a"]
    assert len(registry.list(limit=999)) == 2


# ── b) FakeRedis：持久化、server 映射、重启恢复 ──────────────────────────────

def test_tasks_redis_persistence_and_restore(isolated):
    registry = tasks_mod.task_registry
    task = registry.create("t-redis", "agent_deploy", server_id="srv-9", meta={"k": "v"})

    persisted = isolated.store["opscenter:task:t-redis"]
    assert persisted["task_id"] == task["task_id"]
    assert isolated.expires["opscenter:task:t-redis"] == 86400
    assert isolated.store["opscenter:agent_task:srv-9"] == "t-redis"
    assert isolated.expires["opscenter:agent_task:srv-9"] == 86400

    registry.finish("t-redis", True, message="done")
    assert isolated.store["opscenter:task:t-redis"]["status"] == "success"

    registry._tasks.clear()
    restored = registry.get("t-redis")
    assert restored["status"] == "success"
    assert restored["message"] == "done"

    registry._tasks.clear()
    by_server = registry.get_by_server("srv-9", task_type="agent_deploy")
    assert by_server["task_id"] == "t-redis"
    assert registry.get_by_server("srv-9", task_type="other") is None


# ── c) agent_tasks 委托 ──────────────────────────────────────────────────────

def test_agent_tasks_delegation(isolated):
    task = AGENT_TASKS.start("srv-1", kind="agent_deploy")
    assert task["task_id"]
    assert task["server_id"] == "srv-1"
    assert task["kind"] == "agent_deploy"
    assert task["status"] == "deploying"
    assert task["phase"] == "部署中"
    assert task["started_at"] > 0
    assert task["updated_at"] >= task["started_at"]
    assert task["message"] == "" and task["error"] == ""
    assert AGENT_TASKS.get("srv-1")["task_id"] == task["task_id"]

    AGENT_TASKS.update("srv-1", phase="校验中", message="Bearer token")
    current = AGENT_TASKS.get("srv-1")
    assert current["phase"] == "校验中"
    assert current["message"] == "***token"

    AGENT_TASKS.finish("srv-1", success=True, message="ok")
    current = AGENT_TASKS.get("srv-1")
    assert current["status"] == "success"
    assert current["phase"] == "成功"
    assert current["message"] == "ok"
    assert AGENT_TASKS.get("no-such-server") is None


def test_agent_tasks_upgrade_kind_and_restart(isolated):
    started = AGENT_TASKS.start("srv-2", kind="upgrade")
    assert started["kind"] == "upgrade"
    assert AGENT_TASKS.get("srv-2")["kind"] == "upgrade"
    AGENT_TASKS.finish("srv-2", success=False, error="boom")

    tasks_mod.task_registry._tasks.clear()
    restored = AGENT_TASKS.get("srv-2")
    assert restored["task_id"] == started["task_id"]
    assert restored["kind"] == "upgrade"
    assert restored["status"] == "failed"
    assert restored["error"] == "boom"


def test_agent_tasks_restores_legacy_record_without_kind(isolated):
    isolated.store["opscenter:task:legacy"] = {
        "task_id": "legacy", "task_type": "agent_deploy", "server_id": "srv-3",
        "status": "success", "phase": "成功", "message": "", "error": "",
        "meta": {}, "created_at": 1.0, "updated_at": 2.0,
    }
    isolated.store["opscenter:agent_task:srv-3"] = "legacy"
    tasks_mod.task_registry._tasks.clear()

    restored = AGENT_TASKS.get("srv-3")
    assert restored["task_id"] == "legacy"
    assert restored["kind"] == "agent_deploy"


# ── d) mq：publish / consume / ack / nack ────────────────────────────────────

class FakeMethod:
    def __init__(self, delivery_tag=1):
        self.delivery_tag = delivery_tag


class FakeProperties:
    def __init__(self, delivery_mode=1, content_type=None):
        self.delivery_mode = delivery_mode
        self.content_type = content_type


class FakeChannel:
    def __init__(self):
        self.is_open = True
        self.declared = []
        self.published = []
        self.acked = []
        self.nacked = []
        self.pending = []
        self.closed = False

    def queue_declare(self, queue=None, durable=False, arguments=None, **kwargs):
        self.declared.append({"queue": queue, "durable": durable, "arguments": arguments})

    def basic_publish(self, exchange="", routing_key="", body=None, properties=None):
        self.published.append({
            "exchange": exchange,
            "routing_key": routing_key,
            "body": body,
            "properties": properties,
        })

    def basic_get(self, queue=None, auto_ack=False):
        if self.pending:
            return self.pending.pop(0)
        return None, None, None

    def basic_ack(self, delivery_tag=None, multiple=False):
        self.acked.append(delivery_tag)

    def basic_nack(self, delivery_tag=None, requeue=True, multiple=False):
        self.nacked.append((delivery_tag, requeue))

    def close(self):
        self.is_open = False
        self.closed = True


class FakeConnection:
    def __init__(self, channel):
        self.channel_obj = channel
        self.is_open = True
        self.closed = False

    def channel(self):
        return self.channel_obj

    def close(self):
        self.is_open = False
        self.closed = True


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeHttpx:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, auth=None, timeout=None):
        self.calls.append({"url": url, "auth": auth, "timeout": timeout})
        return FakeResponse(self.payload)


def _install_transport(monkeypatch, url="amqp://user:s3cret@127.0.0.1:5672/"):
    channel = FakeChannel()
    connection = FakeConnection(channel)
    monkeypatch.setattr(mq_mod, "_open_connection", lambda target: connection)
    monkeypatch.setattr(mq_mod, "MQ_ENABLED", True)
    monkeypatch.setattr(mq_mod, "MQ_URL", url)
    task_queue._reset_transport()
    return channel


def _envelope(delivery_tag, task_type, task_id="t-1"):
    payload = {
        "task_id": task_id,
        "task_type": task_type,
        "server_id": "s1",
        "payload": {},
    }
    return FakeMethod(delivery_tag), FakeProperties(), json.dumps(payload).encode("utf-8")


def test_mq_publish_unavailable_returns_none(monkeypatch):
    monkeypatch.setattr(mq_mod, "MQ_ENABLED", True)
    monkeypatch.setattr(mq_mod, "MQ_URL", "")
    assert task_queue.publish("agent_deploy", {"a": 1}) is None

    monkeypatch.setattr(mq_mod, "MQ_ENABLED", False)
    monkeypatch.setattr(mq_mod, "MQ_URL", "amqp://guest:guest@127.0.0.1:5672/")
    assert task_queue.publish("agent_deploy", {"a": 1}) is None
    assert task_queue.consume_once(timeout=0.01) is None


def test_mq_publish_persistent_message(monkeypatch):
    channel = _install_transport(monkeypatch)
    result = task_queue.publish("agent_deploy", {"server_id": "s1"}, server_id="s1")
    assert result is not None
    assert result["queued"] is True
    assert result["queue"] == "opscenter.test.tasks"
    assert result["task_id"]

    assert len(channel.published) == 1
    item = channel.published[0]
    assert item["exchange"] == ""
    assert item["routing_key"] == "opscenter.test.tasks"
    assert item["properties"].delivery_mode == 2
    message = json.loads(item["body"].decode("utf-8"))
    assert message["task_id"] == result["task_id"]
    assert message["task_type"] == "agent_deploy"
    assert message["server_id"] == "s1"
    assert message["payload"] == {"server_id": "s1"}
    assert "ts" in message

    declared = {entry["queue"]: entry for entry in channel.declared}
    main = declared["opscenter.test.tasks"]
    assert main["durable"] is True
    assert main["arguments"]["x-dead-letter-exchange"] == ""
    assert main["arguments"]["x-dead-letter-routing-key"] == "opscenter.test.tasks.dlq"
    assert declared["opscenter.test.tasks.dlq"]["durable"] is True

    tracked = tasks_mod.task_registry.get(result["task_id"])
    assert tracked["status"] == "queued"
    assert tracked["task_type"] == "agent_deploy"


def test_mq_consume_once_ack_and_registry_running(monkeypatch):
    channel = _install_transport(monkeypatch)
    received = []
    task_queue.register_handler("agent_deploy", lambda message: received.append(message))
    queued = task_queue.publish("agent_deploy", {"server_id": "s1"}, server_id="s1")
    channel.pending.append(_envelope(11, "agent_deploy", task_id=queued["task_id"]))

    message = task_queue.consume_once(timeout=0.2)
    assert message is not None
    assert message["task_id"] == queued["task_id"]
    assert received and received[0]["task_type"] == "agent_deploy"
    assert channel.acked == [11]
    assert channel.nacked == []
    assert tasks_mod.task_registry.get(queued["task_id"])["status"] == "running"


def test_mq_consume_once_handler_error_nacks_without_requeue(monkeypatch):
    channel = _install_transport(monkeypatch)

    def boom(message):
        raise RuntimeError("handler exploded")

    task_queue.register_handler("agent_deploy", boom)
    channel.pending.append(_envelope(12, "agent_deploy", task_id="t-fail"))

    message = task_queue.consume_once(timeout=0.2)
    assert message is not None
    assert channel.acked == []
    assert channel.nacked == [(12, False)]


def test_mq_consume_once_missing_handler_nacks(monkeypatch):
    channel = _install_transport(monkeypatch)
    channel.pending.append(_envelope(13, "unknown-type", task_id="t-none"))
    task_queue.consume_once(timeout=0.2)
    assert channel.acked == []
    assert channel.nacked == [(13, False)]


def test_mq_status_worker_lifecycle_and_masking(monkeypatch):
    _install_transport(monkeypatch)
    status = task_queue.status()
    assert status["kind"] == "rabbitmq"
    assert status["configured"] is True
    assert status["available"] is True
    assert status["latency_ms"] is not None
    assert status["detail"]["endpoint"] == "127.0.0.1:5672"
    assert status["detail"]["queue"] == "opscenter.test.tasks"
    assert status["detail"]["dlq"] == "opscenter.test.tasks.dlq"
    assert "s3cret" not in json.dumps(status, ensure_ascii=False)

    assert task_queue.start_worker() is True
    assert task_queue.start_worker() is True  # 幂等
    deadline = time.monotonic() + 1.0
    while not task_queue._worker_running and time.monotonic() < deadline:
        time.sleep(0.01)
    assert task_queue.status()["detail"]["worker_running"] is True
    task_queue.stop_worker(timeout=2.0)
    assert task_queue._worker_running is False


# ── e) queues()：Management API 解析与降级 ───────────────────────────────────

def test_mq_queues_without_management_and_unparsable(monkeypatch):
    monkeypatch.setattr(mq_mod, "MQ_MANAGEMENT_URL", "")
    monkeypatch.setattr(mq_mod, "MQ_URL", "")
    assert task_queue.queues() == []

    monkeypatch.setattr(mq_mod, "MQ_URL", "not a url")
    assert task_queue.queues() == []


def test_mq_queues_management_mapping(monkeypatch):
    stub = FakeHttpx([{
        "name": "opscenter.tasks", "messages": 3, "messages_ready": 2,
        "messages_unacknowledged": 1, "consumers": 1, "state": "running",
        "node": "rabbit@node1",
    }])
    monkeypatch.setattr(mq_mod, "httpx", stub)
    monkeypatch.setattr(mq_mod, "MQ_MANAGEMENT_URL", "rabbitmq.local:15672")
    monkeypatch.setattr(mq_mod, "MQ_URL", "amqp://user:s3cret@rabbitmq.local:5672/")

    queues = task_queue.queues()
    assert queues == [{
        "name": "opscenter.tasks", "messages": 3, "messages_ready": 2,
        "messages_unacknowledged": 1, "consumers": 1, "state": "running",
    }]
    assert stub.calls[0]["url"] == "http://rabbitmq.local:15672/api/queues"
    assert stub.calls[0]["auth"] == ("user", "s3cret")


def test_mq_queues_derived_from_mq_url(monkeypatch):
    stub = FakeHttpx([])
    monkeypatch.setattr(mq_mod, "httpx", stub)
    monkeypatch.setattr(mq_mod, "MQ_MANAGEMENT_URL", "")
    monkeypatch.setattr(mq_mod, "MQ_URL", "amqp://user:s3cret@rabbit.internal:5672/")
    assert task_queue.queues() == []
    assert stub.calls[0]["url"] == "http://rabbit.internal:15672/api/queues"
