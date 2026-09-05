"""Long-term metric rollup and time-range API tests."""

import os
import sys
from datetime import datetime, timedelta
import uuid

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://opscenter:opscenter123@127.0.0.1:5433/opscenter_test",
)
os.environ["OPS_AUTH_ENABLED"] = "false"
os.environ["LOCAL_HOST"] = "127.0.0.1"

sys.path.insert(0, "/opt/opscenter/backend")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import Base, SessionLocal, app, engine  # noqa: E402
from app.metrics_history import build_metric_rollups  # noqa: E402
from app.models import MetricHistory, MetricRollup, Server  # noqa: E402


client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def _seed_metrics():
    db = SessionLocal()
    try:
        server = Server(name="history-host", host="10.66.66.99", ssh_key="__password__x")
        db.add(server)
        db.flush()
        samples = [
            (datetime(2026, 8, 30, 11, 56), "cpu", 30.0),
            (datetime(2026, 8, 30, 12, 0), "cpu", 10.0),
            (datetime(2026, 8, 30, 12, 2), "cpu", 20.0),
            (datetime(2026, 8, 30, 12, 4), "cpu", 40.0),
            (datetime(2026, 8, 30, 12, 1), "memory", 60.0),
        ]
        for timestamp, metric, value in samples:
            db.add(MetricHistory(server_id=server.id, timestamp=timestamp, metric=metric, value=value))
        db.commit()
        return str(server.id)
    finally:
        db.close()


def test_rollups_are_accurate_and_idempotent():
    server_id = _seed_metrics()
    now = datetime(2026, 8, 30, 12, 17)

    first = build_metric_rollups(now=now)
    second = build_metric_rollups(now=now)

    assert first["five_minute"] == 3
    # 12:00-12:59 is still incomplete at 12:17, so only the 11:00 bucket is persisted.
    assert first["hourly"] == 1
    assert second == first
    db = SessionLocal()
    try:
        assert db.query(MetricRollup).count() == 4
        bucket = db.query(MetricRollup).filter(
            MetricRollup.server_id == uuid.UUID(server_id),
            MetricRollup.metric == "cpu",
            MetricRollup.resolution == "5m",
            MetricRollup.bucket_at == datetime(2026, 8, 30, 12, 0),
        ).one()
        assert bucket.value_avg == pytest.approx(70 / 3)
        assert bucket.value_min == 10
        assert bucket.value_max == 40
        assert bucket.sample_count == 3
    finally:
        db.close()


def test_hourly_rollup_combines_multiple_five_minute_buckets():
    db = SessionLocal()
    try:
        server = Server(name="dense-host", host="10.66.66.98", ssh_key="__password__x")
        db.add(server)
        db.flush()
        db.add_all([
            MetricHistory(server_id=server.id, timestamp=datetime(2026, 8, 30, 11, 1), metric="cpu", value=10),
            MetricHistory(server_id=server.id, timestamp=datetime(2026, 8, 30, 11, 6), metric="cpu", value=30),
        ])
        db.commit()
        server_id = server.id
    finally:
        db.close()

    build_metric_rollups(now=datetime(2026, 8, 30, 12, 17))
    with SessionLocal() as db:
        rows = db.query(MetricRollup).filter(
            MetricRollup.server_id == server_id,
            MetricRollup.metric == "cpu",
            MetricRollup.resolution == "1h",
        ).all()
        assert len(rows) == 1
        assert rows[0].value_avg == pytest.approx(20)
        assert rows[0].sample_count == 2


def test_timeseries_supports_raw_rollup_and_validation():
    server_id = _seed_metrics()
    build_metric_rollups(now=datetime(2026, 8, 30, 12, 17))
    params = {
        "metrics": "cpu,memory",
        "start": "2026-08-30T11:50:00Z",
        "end": "2026-08-30T12:10:00Z",
        "resolution": "5m",
    }
    response = client.get(f"/api/v2/servers/{server_id}/metrics/timeseries", params=params)
    assert response.status_code == 200
    payload = response.json()
    assert payload["resolution"] == "5m"
    assert payload["point_count"] == 3
    assert payload["series"]["cpu"][1][1:] == pytest.approx([70 / 3, 10, 40, 3])

    raw = client.get(
        f"/api/v2/servers/{server_id}/metrics/timeseries",
        params={**params, "resolution": "raw"},
    )
    assert raw.status_code == 200
    assert raw.json()["point_count"] == 5

    invalid = client.get(
        f"/api/v2/servers/{server_id}/metrics/timeseries",
        params={**params, "metrics": "passwords"},
    )
    assert invalid.status_code == 400

    too_long = client.get(
        f"/api/v2/servers/{server_id}/metrics/timeseries",
        params={**params, "start": "2020-01-01T00:00:00Z"},
    )
    assert too_long.status_code == 400


def test_host_overview_aggregates_all_hosts_in_one_request():
    server_id = _seed_metrics()
    build_metric_rollups(now=datetime(2026, 8, 30, 12, 17))
    db = SessionLocal()
    try:
        empty = Server(name="empty-host", host="10.66.66.100", ssh_key="__password__x")
        db.add(empty)
        db.commit()
        empty_id = str(empty.id)
    finally:
        db.close()

    params = {
        "metrics": "cpu,memory,disk",
        "start": "2026-08-30T11:50:00Z",
        "end": "2026-08-30T12:10:00Z",
        "resolution": "raw",
    }
    response = client.get("/api/v2/metrics/hosts/overview", params=params)
    assert response.status_code == 200
    payload = response.json()
    assert payload["resolution"] == "raw"
    hosts = {item["server_id"]: item for item in payload["hosts"]}
    assert set(hosts) == {server_id, empty_id}
    cpu = hosts[server_id]["metrics"]["cpu"]
    assert cpu["average"] == pytest.approx(25.0)
    assert cpu["minimum"] == 10
    assert cpu["maximum"] == 40
    assert cpu["latest"] == 40
    assert cpu["samples"] == 4
    assert cpu["last_at"] == "2026-08-30T12:04:00Z"
    assert hosts[empty_id]["metrics"] == {}

    rollup = client.get("/api/v2/metrics/hosts/overview", params={**params, "resolution": "5m"})
    assert rollup.status_code == 200
    rollup_cpu = {item["server_id"]: item for item in rollup.json()["hosts"]}[server_id]["metrics"]["cpu"]
    assert rollup_cpu["average"] == pytest.approx(25.0)
    assert rollup_cpu["samples"] == 4
