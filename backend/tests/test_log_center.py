"""Host-scoped Loki gateway tests."""

import os
import sys

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

from app import log_center  # noqa: E402
from app.main import Base, SessionLocal, app, engine  # noqa: E402
from app.models import Server  # noqa: E402


client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_database(monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(log_center, "LOKI_URL", "http://loki.test:3100")
    yield
    Base.metadata.drop_all(bind=engine)


def _server_id():
    db = SessionLocal()
    try:
        server = Server(name="log-host", host="10.66.66.80", ssh_key="__password__x")
        db.add(server)
        db.commit()
        db.refresh(server)
        return str(server.id)
    finally:
        db.close()


class _Response:
    ok = True
    text = "ready"

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "status": "success",
            "data": {"result": [{
                "stream": {"server_id": "hidden", "source": "journal", "service_name": "sshd.service", "secret": "drop"},
                "values": [["1788076800000000000", "accepted connection"]],
            }]},
        }


def test_query_is_scoped_to_host_and_sanitizes_labels(monkeypatch):
    server_id = _server_id()
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured.update({"url": url, "params": params, "timeout": timeout})
        return _Response()

    monkeypatch.setattr(log_center.requests, "get", fake_get)
    response = client.get(
        f"/api/v2/servers/{server_id}/logs/query",
        params={
            "start": "2026-08-30T00:00:00Z", "end": "2026-08-30T01:00:00Z",
            "source": "journal", "service": "sshd.service", "search": "accepted",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert captured["params"]["query"] == f'{{server_id="{server_id}",source="journal",service_name="sshd.service"}} |= "accepted"'
    assert payload["count"] == 1
    assert payload["entries"][0]["labels"] == {"source": "journal", "service_name": "sshd.service"}


def test_query_rejects_unbounded_or_injected_input(monkeypatch):
    server_id = _server_id()
    injected = client.get(
        f"/api/v2/servers/{server_id}/logs/query",
        params={"service": 'sshd"} or {source="docker'},
    )
    assert injected.status_code == 400

    too_long = client.get(
        f"/api/v2/servers/{server_id}/logs/query",
        params={"start": "2024-01-01T00:00:00Z", "end": "2026-08-30T00:00:00Z"},
    )
    assert too_long.status_code == 400


def test_status_handles_unconfigured_loki(monkeypatch):
    monkeypatch.setattr(log_center, "LOKI_URL", "")
    response = client.get("/api/v2/logs/status")
    assert response.status_code == 200
    assert response.json()["configured"] is False


def test_stats_aggregates_sources_levels_and_services(monkeypatch):
    server_id = _server_id()
    queries = []

    class VectorResponse(_Response):
        def __init__(self, query):
            self.query = query

        def json(self):
            if "|~" in self.query:
                result = [{"metric": {}, "value": [0, "7"]}]
            elif "by (source)" in self.query:
                result = [{"metric": {"source": "journal"}, "value": [0, "80"]}, {"metric": {"source": "docker"}, "value": [0, "20"]}]
            elif "by (level)" in self.query:
                result = [{"metric": {"level": "error"}, "value": [0, "7"]}, {"metric": {"level": "info"}, "value": [0, "93"]}]
            elif "by (service_name)" in self.query:
                result = [{"metric": {"service_name": "sshd.service"}, "value": [0, "60"]}, {"metric": {"service_name": "nginx"}, "value": [0, "40"]}]
            else:
                result = [{"metric": {}, "value": [0, "100"]}]
            return {"status": "success", "data": {"result": result}}

    def fake_get(url, params=None, timeout=None):
        queries.append(params["query"])
        return VectorResponse(params["query"])

    monkeypatch.setattr(log_center.requests, "get", fake_get)
    response = client.get(
        f"/api/v2/servers/{server_id}/logs/stats",
        params={"start": "2026-08-30T00:00:00Z", "end": "2026-08-30T01:00:00Z"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 100
    assert payload["error_count"] == 7
    assert payload["sources"] == {"journal": 80, "docker": 20}
    assert payload["top_services"][0] == {"name": "sshd.service", "count": 60}
    assert len(queries) == 5
    assert all(f'server_id="{server_id}"' in query for query in queries)


def test_timeseries_uses_safe_host_scope_and_auto_buckets(monkeypatch):
    server_id = _server_id()
    calls = []

    class MatrixResponse(_Response):
        def __init__(self, query):
            self.query = query

        def json(self):
            values = [["1788076800", "2"], ["1788077100", "3"]]
            if "|~" in self.query:
                values = [["1788076800", "1"], ["1788077100", "0"]]
            return {"status": "success", "data": {"result": [{"metric": {}, "values": values}]}}

    def fake_get(url, params=None, timeout=None):
        calls.append({"url": url, "params": params})
        return MatrixResponse(params["query"])

    monkeypatch.setattr(log_center.requests, "get", fake_get)
    response = client.get(
        f"/api/v2/servers/{server_id}/logs/timeseries",
        params={"start": "2026-08-30T00:00:00Z", "end": "2026-08-30T08:00:00Z", "source": "docker"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["step_seconds"] == 300
    assert payload["series"]["total"] == [[1788076800, 2.0], [1788077100, 3.0]]
    assert payload["series"]["errors"][0] == [1788076800, 1.0]
    assert len(calls) == 2
    assert all(call["url"].endswith("/loki/api/v1/query_range") for call in calls)
    assert all(call["params"]["step"] == 300 for call in calls)
    assert all(f'server_id="{server_id}"' in call["params"]["query"] for call in calls)
    assert all('source="docker"' in call["params"]["query"] for call in calls)


def test_storage_health_parses_loki_metrics_index_and_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(log_center, "LOKI_DATA_DIR", str(tmp_path))

    class HealthResponse:
        def __init__(self, url):
            self.url = url
            self.text = "\n".join([
                "loki_distributor_lines_received_total 120",
                "loki_distributor_bytes_received_total 4096",
                'loki_discarded_samples_total{reason="rate_limited"} 2',
                'loki_request_duration_seconds_count{route="loki_api_v1_push",status_code="503"} 3',
                'loki_request_duration_seconds_count{route="loki_api_v1_query",status_code="503"} 99',
                "loki_distributor_ingester_append_timeouts_total 1",
                "loki_panic_total 0",
                "loki_ingester_flush_queue_length 4",
            ])

        def raise_for_status(self):
            return None

        def json(self):
            if self.url.endswith("/loki/api/v1/index/stats"):
                return {"streams": 4, "chunks": 8, "entries": 300, "bytes": 8192}
            return {"status": "success"}

    monkeypatch.setattr(log_center.requests, "get", lambda url, params=None, timeout=None: HealthResponse(url))
    response = client.get("/api/v2/logs/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["data_dir"]["available"] is True
    assert payload["runtime"]["received_lines_total"] == 120
    assert payload["runtime"]["received_bytes_total"] == 4096
    assert payload["runtime"]["write_5xx_total"] == 3
    assert payload["runtime"]["discarded_samples_total"] == 2
    assert payload["runtime"]["append_timeouts_total"] == 1
    assert payload["runtime"]["flush_queue_length"] == 4
    assert payload["index_24h"]["entries"] == 300
    assert any("累计写入异常指标 6 次" in item for item in payload["warnings"])
    assert any("flush 队列积压 4" in item for item in payload["warnings"])
