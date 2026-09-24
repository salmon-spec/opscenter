"""Data-service monitor API and history contract tests (v5.2.2)."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import auth
from app import data_service_monitor as monitor
from app.main import Base, SessionLocal, app, engine
from app.models import MiddlewareMetricSample

TEST_TOKEN = "test-operator-token"
client = TestClient(app, headers={"Authorization": f"Bearer {TEST_TOKEN}"})
BASE = "/api/v2/data-services"


class FakeMonitorService:
    def __init__(
        self,
        kind: str,
        metrics: list[dict] | None = None,
        *,
        fail: bool = False,
        payload: dict | None = None,
    ):
        self.kind = kind
        self.metrics = metrics or []
        self.fail = fail
        self.payload = payload

    def status(self):
        if self.fail:
            raise RuntimeError(f"{self.kind} status failed")
        return {
            "kind": self.kind,
            "configured": True,
            "available": True,
            "latency_ms": 3.5,
            "error": "",
            "detail": {"endpoint": f"{self.kind}.local:1234", "version": "1.0.0"},
        }

    def monitor_metrics(self):
        if self.fail:
            raise RuntimeError(f"{self.kind} metrics failed")
        if self.payload is not None:
            return self.payload
        return list(self.metrics)


@pytest.fixture(autouse=True)
def setup_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    monitor._CACHE.clear()
    yield
    monitor._CACHE.clear()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def auth_configured(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "MONITOR_ALLOW_ANONYMOUS", False)
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", TEST_TOKEN)


@pytest.fixture()
def fake_services(monkeypatch):
    services = {}
    for kind in monitor._KINDS:
        metrics = []
        if kind == "redis":
            metrics = [
                {
                    "key": "redis_memory_usage_percent",
                    "label": "内存水位",
                    "value": 92.5,
                    "unit": "%",
                    "dimensions": {"instance": "redis.local:6379", "database": "db0"},
                    "source": "INFO",
                },
                {
                    "key": "redis_command_calls_total",
                    "label": "命令调用累计",
                    "value": 120,
                    "unit": "count",
                    "dimensions": {"instance": "redis.local:6379", "command": "get"},
                    "source": "COMMANDSTATS",
                },
            ]
        services[kind] = FakeMonitorService(kind, metrics)
    monkeypatch.setattr(monitor, "_service_for", lambda kind: services.get(kind))
    return services


def test_monitor_endpoints_expose_metrics_dimensions_and_diagnostics(fake_services):
    monitor_body = client.get(f"{BASE}/redis/monitor").json()
    assert monitor_body["partial_errors"] == []
    assert monitor_body["source_status"] == {"redis": True}
    data = monitor_body["data"]
    assert data["kind"] == "redis" and data["available"] is True
    metrics = {(item["key"], item["dimension_key"]): item for item in data["metrics"]}
    memory = next(item for (key, _), item in metrics.items() if key == "redis_memory_usage_percent")
    available = next(item for (key, _), item in metrics.items() if key == "data_service_available")
    assert memory["status"] == "warning"
    assert memory["dimensions"] == {"instance": "redis.local:6379", "database": "db0"}
    assert available["status"] == "ok"
    dimension_keys = {item["key"] for item in data["dimensions"]}
    assert {"instance", "database", "command"} <= dimension_keys

    filtered = client.get(f"{BASE}/redis/metrics", params={"status": "warning"}).json()["data"]
    assert [item["key"] for item in filtered["metrics"]] == ["redis_memory_usage_percent"]

    diagnostics = client.get(f"{BASE}/redis/diagnostics").json()["data"]["items"]
    assert any(item["metric_key"] == "redis_memory_usage_percent" and item["level"] == "warning"
               for item in diagnostics)

    overview = client.get(f"{BASE}/monitor/overview").json()
    assert overview["data"]["summary"]["services_total"] == 7
    assert overview["data"]["summary"]["services_available"] == 7
    assert overview["data"]["summary"]["warning_metrics"] == 1
    assert overview["source_status"] == {kind: True for kind in monitor._KINDS}


def test_monitor_requires_configured_auth(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "MONITOR_ALLOW_ANONYMOUS", False)
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "")
    assert client.get(f"{BASE}/redis/monitor").status_code == 503

    monkeypatch.setattr(auth, "OPERATOR_TOKEN", TEST_TOKEN)
    wrong = TestClient(app, headers={"Authorization": "Bearer wrong-token"})
    assert wrong.get(f"{BASE}/redis/monitor").status_code == 401
    assert client.get(f"{BASE}/redis/monitor").status_code == 200


def test_monitor_anonymous_override_keeps_test_pages_loginless(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "OPERATOR_TOKEN", "")
    monkeypatch.setattr(auth, "MONITOR_ALLOW_ANONYMOUS", True)
    assert TestClient(app).get(f"{BASE}/redis/monitor").status_code == 200


def test_monitor_collector_failure_degrades_to_status_only(monkeypatch):
    service = FakeMonitorService("redis", fail=True)
    monkeypatch.setattr(monitor, "_service_for", lambda kind: service if kind == "redis" else None)

    body = client.get(f"{BASE}/redis/monitor").json()
    data = body["data"]
    assert data["available"] is False
    assert data["quality"] == "unknown"
    assert {"data_service_configured", "data_service_available"} <= {
        item["key"] for item in data["metrics"]
    }
    assert any("status failed" in item for item in body["partial_errors"])
    assert any("metrics failed" in item for item in body["partial_errors"])


def test_structured_collector_errors_preserve_quality(monkeypatch):
    service = FakeMonitorService(
        "redis",
        payload={
            "metrics": [{
                "key": "redis_memory_usage_percent", "label": "内存水位", "value": 20,
                "unit": "%", "dimensions": {"instance": "redis.local:6379"},
            }],
            "errors": ["command stats unavailable"],
        },
    )
    monkeypatch.setattr(monitor, "_service_for", lambda kind: service)

    body = client.get(f"{BASE}/redis/monitor").json()
    assert body["data"]["quality"] == "unknown"
    assert body["data"]["metrics_total"] == 4
    assert body["partial_errors"] == ["redis: command stats unavailable"]


def test_metric_limit_keeps_thresholds_and_fallbacks(monkeypatch):
    metrics = [
        {
            "key": f"redis_detail_{index:03d}", "label": f"detail {index}", "value": index,
            "unit": "count", "dimensions": {"instance": "redis.local:6379"},
        }
        for index in range(299)
    ]
    metrics.append({
        "key": "redis_memory_usage_percent", "label": "内存水位", "value": 99,
        "unit": "%", "dimensions": {"instance": "redis.local:6379"},
    })
    monkeypatch.setattr(monitor, "_service_for", lambda kind: FakeMonitorService(kind, metrics))

    row = monitor._collect_kind("redis")
    keys = [item["key"] for item in row["metrics"]]
    assert len(row["metrics"]) == monitor._SAMPLE_MAX_METRICS_PER_KIND
    assert row["metrics_total"] > monitor._SAMPLE_MAX_METRICS_PER_KIND
    assert row["metrics_truncated"] is True
    assert "redis_memory_usage_percent" in keys
    assert {"data_service_configured", "data_service_available", "data_service_latency_ms"} <= set(keys)


def test_rabbitmq_age_and_alarm_thresholds():
    age = monitor._normalize_metric({
        "key": "rabbitmq_queue_oldest_message_age_seconds",
        "value": 7200,
        "unit": "s",
    }, "rabbitmq")
    assert age["status"] == "critical"

    warning_age = monitor._normalize_metric({
        "key": "rabbitmq_queue_oldest_message_age_seconds",
        "value": 600,
        "unit": "s",
    }, "rabbitmq")
    assert warning_age["status"] == "warning"

    alarm = monitor._normalize_metric({
        "key": "rabbitmq_disk_free_alarm",
        "value": 1,
        "unit": "bool",
    }, "rabbitmq")
    healthy = monitor._normalize_metric({
        "key": "rabbitmq_disk_free_alarm",
        "value": 0,
        "unit": "bool",
    }, "rabbitmq")
    assert alarm["status"] == "critical"
    assert healthy["status"] == "ok"


def test_timeseries_filters_groups_and_aggregates_history():
    rows = [
        MiddlewareMetricSample(
            kind="redis", metric_key="redis_memory_usage_percent", value=80, unit="%",
            dimensions={"instance": "redis.local:6379", "database": "db0"},
            dimension_key=monitor._dimension_key({"instance": "redis.local:6379", "database": "db0"}),
            sampled_at=datetime(2026, 9, 23, 12, 0), resolution="raw", quality="ok", source="INFO",
        ),
        MiddlewareMetricSample(
            kind="redis", metric_key="redis_memory_usage_percent", value=90, unit="%",
            dimensions={"instance": "redis.local:6379", "database": "db0"},
            dimension_key=monitor._dimension_key({"instance": "redis.local:6379", "database": "db0"}),
            sampled_at=datetime(2026, 9, 23, 12, 1), resolution="raw", quality="warning", source="INFO",
        ),
        MiddlewareMetricSample(
            kind="redis", metric_key="redis_memory_usage_percent", value=20, unit="%",
            dimensions={"instance": "redis.local:6380", "database": "db0"},
            dimension_key=monitor._dimension_key({"instance": "redis.local:6380", "database": "db0"}),
            sampled_at=datetime(2026, 9, 23, 12, 1), resolution="raw", quality="ok", source="INFO",
        ),
    ]
    with SessionLocal() as db:
        db.add_all(rows)
        db.commit()

    body = client.get(
        f"{BASE}/redis/timeseries",
        params={
            "metrics": "redis_memory_usage_percent",
            "start": "2026-09-23T11:55:00Z",
            "end": "2026-09-23T12:05:00Z",
            "resolution": "5m",
            "group_by": "database",
            "filters": json.dumps({"instance": "redis.local:6379"}),
        },
    ).json()
    assert body["source_status"] == {"history": True}
    series = body["data"]["series"]
    assert len(series) == 1
    assert series[0]["group"] == "db0"
    assert series[0]["points"][0]["value"] == pytest.approx(85.0)
    assert series[0]["points"][0]["sample_count"] == 2

    empty = client.get(
        f"{BASE}/redis/timeseries",
        params={"metrics": "redis_memory_usage_percent", "range": "15m"},
    ).json()
    assert empty["source_status"] == {"history": False}
    assert empty["partial_errors"] == ["history: 暂无采样数据"]


def test_timeseries_normalizes_offsets_utc_buckets_and_end_only_range():
    assert monitor._parse_datetime(
        "2026-09-23T20:00:00+08:00", datetime(2026, 1, 1),
    ) == datetime(2026, 9, 23, 12, 0)
    assert monitor._bucket_at(datetime(2026, 9, 23, 12, 7), 300) == datetime(2026, 9, 23, 12, 5)

    row = MiddlewareMetricSample(
        kind="redis", metric_key="redis_memory_usage_percent", value=55, unit="%",
        dimensions={"instance": "redis.local:6379"},
        dimension_key=monitor._dimension_key({"instance": "redis.local:6379"}),
        sampled_at=datetime(2026, 9, 23, 11, 30), resolution="raw", quality="ok", source="INFO",
    )
    with SessionLocal() as db:
        db.add(row)
        db.commit()

    body = client.get(
        f"{BASE}/redis/timeseries",
        params={
            "metrics": "redis_memory_usage_percent",
            "range": "1h",
            "end": "2026-09-23T12:00:00Z",
        },
    ).json()
    assert body["data"]["start"] == "2026-09-23T11:00:00Z"
    assert body["data"]["end"] == "2026-09-23T12:00:00Z"
    assert len(body["data"]["series"]) == 1


def test_sampling_is_idempotent_within_the_same_minute(monkeypatch):
    rows = [{
        "kind": "redis",
        "quality": "ok",
        "metrics": [
            {
                "key": "redis_memory_usage_percent", "value": 75.0, "unit": "%",
                "dimensions": {"instance": "redis.local:6379"}, "dimension_key": '{"instance":"redis.local:6379"}',
                "numeric": True, "status": "ok", "source": "INFO",
            },
            {
                "key": "redis_keys_total", "value": 12, "unit": "count",
                "dimensions": {"instance": "redis.local:6379"}, "dimension_key": '{"instance":"redis.local:6379"}',
                "numeric": True, "status": "ok", "source": "INFO",
            },
        ],
    }]
    monkeypatch.setattr(monitor, "_collect_parallel", lambda force=False: rows)

    first = monitor.sample_middleware_metrics()
    second = monitor.sample_middleware_metrics()

    assert first["samples"] == second["samples"] == 2
    with SessionLocal() as db:
        assert db.query(MiddlewareMetricSample).count() == 2


def test_failed_samples_do_not_write_synthetic_zero(monkeypatch):
    rows = [{
        "kind": "redis",
        "quality": "unknown",
        "metrics": [{
            "key": "redis_memory_usage_percent", "value": 0, "unit": "%",
            "dimensions": {"instance": "redis.local:6379"},
            "dimension_key": '{"instance":"redis.local:6379"}',
            "numeric": True, "status": "unknown", "source": "status",
        }],
    }]
    monkeypatch.setattr(monitor, "_collect_parallel", lambda force=False: rows)

    result = monitor.sample_middleware_metrics()
    assert result["samples"] == 0
    with SessionLocal() as db:
        assert db.query(MiddlewareMetricSample).count() == 0


def test_sampling_reentrancy_is_skipped(monkeypatch):
    called = False

    def collect(force=False):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(monitor, "_collect_parallel", collect)
    assert monitor._SAMPLE_LOCK.acquire(blocking=False)
    try:
        result = monitor.sample_middleware_metrics()
    finally:
        monitor._SAMPLE_LOCK.release()

    assert result["skipped"] is True
    assert result["samples"] == 0
    assert called is False


def test_timeseries_validation_and_unknown_kind():
    invalid_resolution = client.get(
        f"{BASE}/redis/timeseries", params={"resolution": "2m"},
    )
    assert invalid_resolution.status_code == 422
    assert client.get(f"{BASE}/clickhouse/monitor").status_code == 404
    assert client.get(f"{BASE}/clickhouse/timeseries").status_code == 404
