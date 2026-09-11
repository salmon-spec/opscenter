"""v5.0.0 Redis 客户端（BE-1）单元测试：不连真实 Redis，全部打桩。

运行（在 backend 目录）：
  $env:DATABASE_URL='sqlite:///./.tmp/test-v50-redis.db'
  python -m pytest tests/test_v50_redis_store.py -v

说明：
- FakeRedis 按 redis 5.x 同步客户端调用签名实现本模块用到的子集；
- 通过 monkeypatch 模块级常量（MW_ENABLED/REDIS_URL/MW_PROBE_TIMEOUT/_TOKEN）
  与单例内部 _client/_status_cache，完全避免网络与真实服务依赖。
"""
import fnmatch
import json
import time

import pytest

from app.services import redis_store as mod
from app.services.redis_store import redis_store

URL = "redis://:s3cret@redis.example.com:6380/3"


class FakePipeline:
    """模拟 redis-py pipeline：链式排队 + execute 顺序取结果。"""

    def __init__(self, client):
        self._client = client
        self._ops = []

    def type(self, key):
        self._ops.append(("type", key))
        return self

    def ttl(self, key):
        self._ops.append(("ttl", key))
        return self

    def execute(self):
        return [getattr(self._client, name)(arg) for name, arg in self._ops]


class FakeRedis:
    """模拟 redis.Redis（decode_responses=True）同步客户端。"""

    def __init__(self, ping_error=None):
        self.store = {}
        self.expires = {}
        self.ping_error = ping_error
        self.ping_calls = 0
        self.last_scan_count = None

    def _purge(self, key):
        deadline = self.expires.get(key)
        if deadline is not None and deadline <= time.monotonic():
            self.store.pop(key, None)
            self.expires.pop(key, None)

    def ping(self):
        self.ping_calls += 1
        if self.ping_error:
            raise ConnectionError(self.ping_error)
        return True

    def get(self, key):
        self._purge(key)
        return self.store.get(key)

    def set(self, key, value, ex=None, px=None, nx=False):
        self._purge(key)
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex:
            self.expires[key] = time.monotonic() + ex
        elif px:
            self.expires[key] = time.monotonic() + px / 1000.0
        else:
            self.expires.pop(key, None)
        return True

    def delete(self, *keys):
        count = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                self.expires.pop(key, None)
                count += 1
        return count

    def incr(self, key):
        self._purge(key)
        value = int(self.store.get(key, 0)) + 1
        self.store[key] = str(value)
        return value

    def expire(self, key, ttl):
        self._purge(key)
        if key not in self.store:
            return False
        self.expires[key] = time.monotonic() + int(ttl)
        return True

    def ttl(self, key):
        self._purge(key)
        if key not in self.store:
            return -2
        if key not in self.expires:
            return -1
        return max(0, int(self.expires[key] - time.monotonic()))

    def type(self, key):
        self._purge(key)
        return "string" if key in self.store else "none"

    def scan(self, cursor=0, match="*", count=100):
        self.last_scan_count = count
        keys = [key for key in sorted(self.store) if fnmatch.fnmatch(key, match)]
        return (0, keys)

    def info(self, section=None):
        return {"redis_version": "7.2.4", "role": "master"}

    def dbsize(self):
        return len(self.store)

    def eval(self, script, numkeys, *args):
        key, token = args[0], args[1]
        if self.store.get(key) == token:
            self.delete(key)
            return 1
        return 0

    def pipeline(self, transaction=False):
        return FakePipeline(self)


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    """每个用例重置模块级配置与单例状态，测试结束自动还原。"""
    monkeypatch.setattr(mod, "MW_ENABLED", True)
    monkeypatch.setattr(mod, "REDIS_URL", "")
    monkeypatch.setattr(mod, "MW_PROBE_TIMEOUT", 1.0)
    monkeypatch.setattr(mod, "_TOKEN", "test-token")
    monkeypatch.setattr(redis_store, "_client", None)
    monkeypatch.setattr(redis_store, "_status_cache", None)
    monkeypatch.setattr(redis_store, "_status_at", 0.0)
    yield


def _configure(monkeypatch, client):
    monkeypatch.setattr(mod, "REDIS_URL", URL)
    monkeypatch.setattr(redis_store, "_client", client)
    monkeypatch.setattr(redis_store, "_status_cache", None)
    monkeypatch.setattr(redis_store, "_status_at", 0.0)


def test_unconfigured_without_url(monkeypatch):
    monkeypatch.setattr(mod, "MW_ENABLED", True)
    monkeypatch.setattr(mod, "REDIS_URL", "")
    status = redis_store.status()
    assert status["configured"] is False
    assert status["available"] is False
    assert redis_store.available() is False


def test_switch_off_fail_open(monkeypatch):
    monkeypatch.setattr(mod, "MW_ENABLED", False)
    monkeypatch.setattr(mod, "REDIS_URL", URL)
    status = redis_store.status()
    assert status["configured"] is False
    assert status["available"] is False
    assert redis_store.available() is False
    assert redis_store.get("k") is None
    assert redis_store.set("k", "v") is False
    assert redis_store.delete("k") == 0
    assert redis_store.get_json("k") is None
    assert redis_store.set_json("k", {"a": 1}) is False
    assert redis_store.incr("k") is None
    assert redis_store.info() == {}
    assert redis_store.dbsize() == 0
    assert redis_store.scan() == (0, [])
    assert redis_store.key_meta("k") == {}
    assert redis_store.raw() is None
    assert redis_store.leader("leader:x") is False
    with redis_store.lock("x") as acquired:
        assert acquired is False


def test_status_available_and_endpoint_masked(monkeypatch):
    fake = FakeRedis()
    _configure(monkeypatch, fake)
    status = redis_store.status()
    assert status["configured"] is True
    assert status["available"] is True
    assert status["latency_ms"] is not None and status["latency_ms"] >= 0
    assert status["error"] == ""
    assert status["detail"]["endpoint"] == "redis.example.com:6380/3"
    assert status["detail"]["version"] == "7.2.4"
    assert "s3cret" not in json.dumps(status, ensure_ascii=False)
    assert redis_store.available() is True


def test_status_error_masked(monkeypatch):
    fake = FakeRedis(ping_error=f"cannot connect {URL}")
    _configure(monkeypatch, fake)
    status = redis_store.status()
    assert status["available"] is False
    assert status["error"]
    assert "s3cret" not in status["error"]
    assert "***" in status["error"]
    assert "s3cret" not in json.dumps(status, ensure_ascii=False)


def test_status_cached_10s(monkeypatch):
    fake = FakeRedis()
    _configure(monkeypatch, fake)
    redis_store.status()
    redis_store.status()
    assert fake.ping_calls == 1
    redis_store._status_cache = None
    redis_store.status()
    assert fake.ping_calls == 2


def test_get_set_json_delete_roundtrip(monkeypatch):
    fake = FakeRedis()
    _configure(monkeypatch, fake)
    assert redis_store.set("k", "v") is True
    assert redis_store.get("k") == "v"
    assert redis_store.set("k2", "v2", ttl=60) is True
    assert "k2" in fake.expires
    assert redis_store.delete("k", "k2") == 2
    assert redis_store.get("k") is None

    assert redis_store.set_json("j", {"名": "值", "n": 1}) is True
    assert "名" in fake.store["j"]
    assert redis_store.get_json("j") == {"名": "值", "n": 1}
    assert redis_store.get_json("missing") is None
    fake.store["bad"] = "{oops"
    assert redis_store.get_json("bad") is None


def test_incr_sets_ttl_on_create(monkeypatch):
    fake = FakeRedis()
    _configure(monkeypatch, fake)
    assert redis_store.incr("counter") == 1
    assert redis_store.incr("counter") == 2
    assert redis_store.incr("window", ttl=30) == 1
    assert "window" in fake.expires
    assert "counter" not in fake.expires


def test_lock_acquire_and_release(monkeypatch):
    fake = FakeRedis()
    _configure(monkeypatch, fake)
    with redis_store.lock("job", ttl=5) as acquired:
        assert acquired is True
        assert "opscenter:job" in fake.store
        with redis_store.lock("job") as second:
            assert second is False
    assert "opscenter:job" not in fake.store


def test_lock_release_checks_token(monkeypatch):
    fake = FakeRedis()
    _configure(monkeypatch, fake)
    with redis_store.lock("job") as acquired:
        assert acquired is True
        fake.store["opscenter:job"] = "other-owner"
    assert fake.store["opscenter:job"] == "other-owner"


def test_lock_blocking_timeout(monkeypatch):
    fake = FakeRedis()
    _configure(monkeypatch, fake)
    fake.store["opscenter:job"] = "someone-else"
    with redis_store.lock("job", ttl=5, blocking_timeout=0.25) as acquired:
        assert acquired is False


def test_leader_acquire_renew_and_preempt(monkeypatch):
    fake = FakeRedis()
    _configure(monkeypatch, fake)
    key = "opscenter:leader:mq-worker"
    assert redis_store.leader("leader:mq-worker") is True
    assert fake.store[key] == "test-token"
    deadline_before = fake.expires[key]
    assert redis_store.leader("leader:mq-worker") is True  # 同 token 续约
    assert fake.store[key] == "test-token"
    assert fake.expires[key] >= deadline_before
    monkeypatch.setattr(mod, "_TOKEN", "other-token")
    assert redis_store.leader("leader:mq-worker") is False  # 不同 token 不能抢占
    assert fake.store[key] == "test-token"


def test_scan_and_key_meta(monkeypatch):
    fake = FakeRedis()
    _configure(monkeypatch, fake)
    fake.store.update({"user:1": "a", "user:2": "b", "order:1": "c"})
    cursor, keys = redis_store.scan(0, "user:*", 10)
    assert cursor == 0
    assert sorted(keys) == ["user:1", "user:2"]
    redis_store.scan(0, "*", 9999)
    assert fake.last_scan_count == 500
    assert redis_store.key_meta("user:1") == {"key": "user:1", "type": "string", "ttl": -1}
    assert redis_store.key_meta("missing") == {"key": "missing", "type": "none", "ttl": -2}


def test_info_dbsize_raw(monkeypatch):
    fake = FakeRedis()
    _configure(monkeypatch, fake)
    assert redis_store.info()["redis_version"] == "7.2.4"
    assert redis_store.info("server")["redis_version"] == "7.2.4"
    assert redis_store.dbsize() == 0
    fake.store["k"] = "v"
    assert redis_store.dbsize() == 1
    assert redis_store.raw() is fake
