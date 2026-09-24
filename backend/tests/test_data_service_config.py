"""Data-service editable connection configuration tests (v5.2.3)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import auth
from app import config
from app import data_service_config as config_mod
from app.credential_crypto import decrypt_secret
from app.database import Base, engine, get_db
from app.main import app
from app.models import DataServiceConfig
from app.services import event_bus as event_bus_mod
from app.services import mq as mq_mod
from app.services import nacos_config as nacos_mod
from app.services import object_store as minio_mod
from app.services import redis_store as redis_mod
from app.services import zk_view as zk_mod

client = TestClient(app)
BASE = "/api/v2/data-services"

VALID_PAYLOADS = {
    "redis": {"url": "redis://:redis-secret@redis.local:6379/2", "enabled": True},
    "rabbitmq": {
        "url": "amqp://ops:mq-secret@mq.local:5672/",
        "management_url": "http://ops:mq-secret@mq.local:15672",
        "queue": "tasks",
        "dlq": "tasks.dlq",
        "enabled": True,
    },
    "kafka": {"bootstrap": "kafka-1.local:9092,kafka-2.local:9092",
              "topic": "ops.events", "enabled": True},
    "zookeeper": {"hosts": "zk-1.local:2181,zk-2.local:2181", "enabled": True},
    "nacos": {
        "url": "http://nacos.local:8848",
        "group": "OPS",
        "config_data_id": "opscenter.test.json",
        "username": "nacos",
        "password": "nacos-secret",
        "enabled": True,
    },
    "minio": {
        "endpoint": "https://minio.local:9000",
        "access_key": "minio-access",
        "secret_key": "minio-secret",
        "bucket": "opscenter-test",
        "secure": True,
        "enabled": True,
    },
    "mongodb": {
        "url": "mongodb://ops:mongo-secret@mongo.local:27017/?authSource=admin",
        "db": "opscenter_test",
        "enabled": True,
    },
}


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "")
    yield
    for kind, values in config_mod._ENV_DEFAULTS.items():
        config_mod._apply_kind(kind, dict(values))
    Base.metadata.drop_all(bind=engine)


def _stored(kind: str) -> dict:
    with get_db() as db:
        row = db.query(DataServiceConfig).filter(DataServiceConfig.kind == kind).first()
        assert row is not None and row.config_ciphertext
        return json.loads(decrypt_secret(row.config_ciphertext))


def test_config_overview_lists_all_kinds_and_redacts_environment_secret(monkeypatch):
    defaults = {key: dict(value) for key, value in config_mod._ENV_DEFAULTS.items()}
    defaults["redis"] = {"url": "redis://:env-secret@env-redis:6379/0", "enabled": True}
    monkeypatch.setattr(config_mod, "_ENV_DEFAULTS", defaults)

    response = client.get(f"{BASE}/config")
    assert response.status_code == 200
    items = {item["kind"]: item for item in response.json()["items"]}
    assert set(items) == set(config_mod._KINDS)
    assert items["redis"]["source"] == "environment"
    assert items["redis"]["configured"] is True
    assert items["redis"]["values"]["url"] == ""
    assert items["redis"]["secret_configured"]["url"] is True
    assert "env-secret" not in response.text
    assert len(items["redis"]["fields"]) >= 2


@pytest.mark.parametrize("kind", list(VALID_PAYLOADS))
def test_each_kind_saves_encrypted_and_applies_immediately(kind):
    response = client.put(f"{BASE}/{kind}/config", json=VALID_PAYLOADS[kind])
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == kind
    assert body["source"] == "database"
    assert body["configured"] is True
    assert body["updated_at"]
    assert all(value == "" for key, value in body["values"].items()
               if key in body["secret_configured"])

    stored = _stored(kind)
    assert stored["enabled"] is True
    raw_ciphertext = ""
    with get_db() as db:
        raw_ciphertext = db.query(DataServiceConfig).filter(
            DataServiceConfig.kind == kind
        ).first().config_ciphertext
    for value in VALID_PAYLOADS[kind].values():
        if isinstance(value, str) and "secret" in value:
            assert value not in raw_ciphertext

    if kind == "redis":
        assert redis_mod.REDIS_URL == VALID_PAYLOADS[kind]["url"]
        assert redis_mod.redis_store._client is None
    elif kind == "rabbitmq":
        assert mq_mod.MQ_URL == VALID_PAYLOADS[kind]["url"]
        assert mq_mod.MQ_ENABLED is True
    elif kind == "kafka":
        assert config.KAFKA_BOOTSTRAP == VALID_PAYLOADS[kind]["bootstrap"]
        assert event_bus_mod._admin is None
    elif kind == "zookeeper":
        assert zk_mod.ZOOKEEPER_HOSTS == VALID_PAYLOADS[kind]["hosts"]
    elif kind == "nacos":
        assert nacos_mod.NACOS_URL == VALID_PAYLOADS[kind]["url"]
        assert nacos_mod.NACOS_PASSWORD == VALID_PAYLOADS[kind]["password"]
    elif kind == "minio":
        assert minio_mod.MINIO_ENDPOINT == "minio.local:9000"
        assert minio_mod.MINIO_SECURE is True
    elif kind == "mongodb":
        assert config.MONGO_URL == VALID_PAYLOADS[kind]["url"]
        assert config.MONGO_DB == VALID_PAYLOADS[kind]["db"]


def test_blank_secret_keeps_previous_value():
    first = client.put(f"{BASE}/redis/config", json=VALID_PAYLOADS["redis"])
    assert first.status_code == 200

    second = client.put(f"{BASE}/redis/config", json={"url": "", "enabled": False})
    assert second.status_code == 200
    assert second.json()["enabled"] is False
    assert second.json()["secret_configured"]["url"] is True
    stored = _stored("redis")
    assert stored["url"] == VALID_PAYLOADS["redis"]["url"]
    assert stored["enabled"] is False


def test_connection_test_uses_unsaved_secret_and_returns_result(monkeypatch):
    seen = {}

    def fake_test(kind, values):
        seen["kind"] = kind
        seen["values"] = dict(values)
        return {"kind": kind, "available": True, "latency_ms": 4.2, "error": ""}

    monkeypatch.setitem(config_mod._TESTERS, "redis", lambda values: (True, 4.2, ""))
    monkeypatch.setattr(config_mod, "_test_connection", fake_test)
    response = client.post(
        f"{BASE}/redis/config/test",
        json={"url": "redis://:new-secret@redis.local:6380/1", "enabled": True},
    )
    assert response.status_code == 200
    assert response.json()["available"] is True
    assert seen == {
        "kind": "redis",
        "values": {"url": "redis://:new-secret@redis.local:6380/1", "enabled": True},
    }
    assert "new-secret" not in response.text


def test_connection_test_redacts_failure(monkeypatch):
    def failed(values):
        raise RuntimeError(f"cannot connect to {values['url']}")

    monkeypatch.setitem(config_mod._TESTERS, "redis", failed)
    response = client.post(
        f"{BASE}/redis/config/test",
        json={"url": "redis://:very-secret@redis.local:6379/0", "enabled": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert "very-secret" not in body["error"]
    assert "***" in body["error"]


def test_reset_removes_database_override_and_restores_environment():
    client.put(f"{BASE}/redis/config", json=VALID_PAYLOADS["redis"])
    response = client.post(f"{BASE}/redis/config/reset")
    assert response.status_code == 200
    expected_source = "environment" if config_mod._ENV_DEFAULTS["redis"]["url"] else "unconfigured"
    assert response.json()["source"] == expected_source
    with get_db() as db:
        assert db.query(DataServiceConfig).filter(DataServiceConfig.kind == "redis").first() is None
    assert redis_mod.REDIS_URL == config_mod._ENV_DEFAULTS["redis"]["url"]


def test_invalid_payload_and_unknown_kind_are_rejected():
    missing = client.put(f"{BASE}/kafka/config", json={"bootstrap": "", "enabled": True})
    assert missing.status_code == 422
    invalid = client.put(f"{BASE}/mongodb/config", json={"url": "http://mongo.local", "db": "ops"})
    assert invalid.status_code == 422
    assert client.get(f"{BASE}/clickhouse/config").status_code == 404
    assert client.put(f"{BASE}/clickhouse/config", json={}).status_code == 404
