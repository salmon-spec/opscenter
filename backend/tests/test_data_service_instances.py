"""Data-service extra monitoring instance contract tests (v5.3.0)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import auth
from app import data_service_instances as instances
from app import data_service_monitor as monitor
from app.credential_crypto import decrypt_secret
from app.database import Base, SessionLocal, engine, get_db
from app.main import app
from app.models import DataServiceConfig, DataServiceInstance, MiddlewareMetricSample

client = TestClient(app)
BASE = "/api/v2/data-services"
REDIS = {
    "name": "缓存从库",
    "url": "redis://:instance-secret@redis-backup.local:6379/1",
    "enabled": True,
    "monitor_enabled": True,
}


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    monitor._CACHE.clear()
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "")
    monkeypatch.setattr(auth, "MONITOR_ALLOW_ANONYMOUS", True)
    yield
    monitor._CACHE.clear()
    Base.metadata.drop_all(bind=engine)


def test_create_list_update_and_redact_extra_instance():
    created = client.post(f"{BASE}/redis/instances", json=REDIS)
    assert created.status_code == 200
    body = created.json()
    instance_id = body["id"]
    assert body["name"] == REDIS["name"]
    assert body["is_primary"] is False
    assert body["values"]["url"] == ""
    assert body["secret_configured"]["url"] is True
    assert "instance-secret" not in created.text

    with get_db() as db:
        row = db.query(DataServiceInstance).filter(
            DataServiceInstance.id == uuid.UUID(instance_id)
        ).one()
        assert "instance-secret" not in row.config_ciphertext
        stored = json.loads(decrypt_secret(row.config_ciphertext))
        assert stored["url"] == REDIS["url"]

    listed = client.get(f"{BASE}/instances", params={"kind": "redis"})
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [instance_id]

    updated = client.put(
        f"{BASE}/instances/{instance_id}",
        json={"url": "", "enabled": False, "monitor_enabled": False},
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
    assert updated.json()["monitor_enabled"] is False
    with get_db() as db:
        row = db.query(DataServiceInstance).filter(
            DataServiceInstance.id == uuid.UUID(instance_id)
        ).one()
        assert json.loads(decrypt_secret(row.config_ciphertext))["url"] == REDIS["url"]


def test_duplicate_name_and_unknown_kind_are_rejected():
    assert client.post(f"{BASE}/redis/instances", json=REDIS).status_code == 200
    duplicate = client.post(f"{BASE}/redis/instances", json=REDIS)
    assert duplicate.status_code == 409
    assert client.post(f"{BASE}/clickhouse/instances", json=REDIS).status_code == 404


def test_unsaved_instance_test_does_not_persist_or_touch_global_client(monkeypatch):
    seen = {}

    def fake_test(kind, values):
        seen["kind"] = kind
        seen["values"] = dict(values)
        return {"kind": kind, "available": True, "latency_ms": 1.5, "error": ""}

    monkeypatch.setattr(instances.profiles, "_test_connection", fake_test)
    monkeypatch.setattr(instances, "collect_instance", lambda *_args: {"metrics": []})
    response = client.post(f"{BASE}/redis/instances/test", json=REDIS)
    assert response.status_code == 200
    assert response.json()["metrics_total"] == 0
    assert seen["values"]["url"] == REDIS["url"]
    assert "instance-secret" not in response.text
    with get_db() as db:
        assert db.query(DataServiceInstance).count() == 0


def test_monitor_collects_selected_extra_instance(monkeypatch):
    created = client.post(f"{BASE}/redis/instances", json=REDIS).json()
    instance_id = created["id"]
    metric = {
        "key": "redis_memory_usage_percent",
        "label": "内存水位",
        "value": 77,
        "unit": "%",
        "dimensions": {"instance": REDIS["name"], "instance_id": instance_id},
        "source": "INFO",
    }
    monkeypatch.setattr(
        instances,
        "collect_instance",
        lambda kind, values, meta: {
            "available": True,
            "latency_ms": 2.0,
            "version": "7.2",
            "endpoint": "redis-backup.local:6379",
            "metrics": [metric],
            "errors": [],
        },
    )
    monitor._CACHE.clear()
    response = client.get(f"{BASE}/redis/monitor", params={"instance": instance_id, "force": True})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["instance_id"] == instance_id
    assert data["instance_name"] == REDIS["name"]
    matching = next(item for item in data["metrics"] if item["key"] == "redis_memory_usage_percent")
    assert matching["value"] == 77
    assert matching["dimensions"]["instance_id"] == instance_id


def test_timeseries_can_filter_by_instance_identity():
    instance_id = client.post(f"{BASE}/redis/instances", json=REDIS).json()["id"]
    other_id = "11111111-1111-1111-1111-111111111111"
    rows = [
        MiddlewareMetricSample(
            kind="redis", metric_key="redis_memory_usage_percent", value=70, unit="%",
            dimensions={"instance_id": instance_id, "instance": REDIS["name"]},
            dimension_key=monitor._dimension_key({"instance_id": instance_id, "instance": REDIS["name"]}),
            sampled_at=datetime(2026, 9, 23, 12, 0), resolution="raw", quality="ok", source="INFO",
        ),
        MiddlewareMetricSample(
            kind="redis", metric_key="redis_memory_usage_percent", value=20, unit="%",
            dimensions={"instance_id": other_id, "instance": "other"},
            dimension_key=monitor._dimension_key({"instance_id": other_id, "instance": "other"}),
            sampled_at=datetime(2026, 9, 23, 12, 0), resolution="raw", quality="ok", source="INFO",
        ),
    ]
    with SessionLocal() as db:
        db.add_all(rows)
        db.commit()

    body = client.get(
        f"{BASE}/redis/timeseries",
        params={
            "metrics": "redis_memory_usage_percent",
            "start": "2026-09-23T11:59:00Z",
            "end": "2026-09-23T12:01:00Z",
            "instance": instance_id,
        },
    ).json()
    assert len(body["data"]["series"]) == 1
    assert body["data"]["series"][0]["points"][0]["value"] == 70


def test_promote_swaps_primary_and_preserves_old_connection(monkeypatch):
    original = {
        "url": "redis://:original-secret@redis-primary.local:6379/0",
        "enabled": True,
    }
    saved = client.put(f"{BASE}/redis/config", json=original)
    assert saved.status_code == 200
    instance_id = client.post(f"{BASE}/redis/instances", json=REDIS).json()["id"]

    monkeypatch.setattr(instances.profiles, "_apply_kind", lambda kind, values: None)
    response = client.post(f"{BASE}/instances/{instance_id}/promote")
    assert response.status_code == 200
    assert response.json()["is_primary"] is True

    with get_db() as db:
        primary = db.query(DataServiceConfig).filter(DataServiceConfig.kind == "redis").one()
        assert json.loads(decrypt_secret(primary.config_ciphertext))["url"] == REDIS["url"]
        rows = db.query(DataServiceInstance).filter(DataServiceInstance.kind == "redis").all()
        assert len(rows) == 1
        assert json.loads(decrypt_secret(rows[0].config_ciphertext))["url"] == original["url"]
