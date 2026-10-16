from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from agent import normalize
from main import app, store


def test_module_version():
    assert TestClient(app).get("/api/v1/version").json()["version"] == "0.2.4"


def test_ingest_requires_token_and_registers_sample(monkeypatch, tmp_path):
    token = tmp_path / "token"
    token.write_text("ingest-test", encoding="utf-8")
    monkeypatch.setenv("HOST_MONITOR_INGEST_TOKEN_FILE", str(token))
    calls = []
    monkeypatch.setattr(store, "save_sample", lambda *args: calls.append(args))
    client = TestClient(app)
    body = [{"id": str(uuid4()), "name": "node", "host": "10.0.0.1",
             "values": {"cpu_percent": 12}}]
    assert client.post("/api/v1/internal/samples", json=body).status_code == 401
    response = client.post("/api/v1/internal/samples", json=body,
                           headers={"X-OpsCenter-Monitor-Token": "ingest-test"})
    assert response.status_code == 200
    assert response.json() == {"accepted": 1}
    assert calls[0][1]["metrics"]["cpu"] == 12


def test_internal_latest_metrics_requires_token(monkeypatch, tmp_path):
    token = tmp_path / "token"
    token.write_text("monitor-test", encoding="utf-8")
    monkeypatch.setenv("OPS_MODULE_INTERNAL_TOKEN_FILE", str(token))
    monkeypatch.setattr(store, "hosts", lambda: [{"id": "host-1", "name": "node", "last_sample_at": None}])
    monkeypatch.setattr(store, "latest", lambda _: {"metrics": {"cpu": 12}})
    client = TestClient(app)
    assert client.get("/api/v1/internal/metrics/latest").status_code == 401
    response = client.get("/api/v1/internal/metrics/latest", headers={"X-OpsCenter-Module-Token": "monitor-test"})
    assert response.status_code == 200
    assert response.json()["items"][0]["metrics"] == {"cpu": 12}


def test_timeseries_reads_module_database(monkeypatch):
    calls = []
    monkeypatch.setattr(store, "host", lambda host_id: {"id": host_id})
    monkeypatch.setattr(store, "series", lambda *args: calls.append(args) or {"cpu": [], "memory": []})
    client = TestClient(app)
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    end = datetime.now(timezone.utc)
    response = client.get(f"/api/v1/hosts/{uuid4()}/metrics/timeseries", params={
        "start": start.isoformat(), "end": end.isoformat(), "metrics": "cpu,memory,cpu",
    })
    assert response.status_code == 200
    assert calls[0][1] == ["cpu", "memory"]
    assert response.json()["series"] == {"cpu": [], "memory": []}


def test_rejects_invalid_host_and_query():
    client = TestClient(app)
    assert client.get("/api/v1/hosts/not-a-uuid/system/summary").status_code == 422
    assert client.get("/api/v1/metrics/hosts/overview", params={"start": "bad", "end": "bad"}).status_code == 400


def test_agent_sample_is_normalized_and_keeps_only_selected_network_fields():
    at = datetime.now(timezone.utc)
    summary, metrics = normalize({
        "cpu_percent": 24.5, "memory_percent": 62, "disk_percent": 38,
        "network_summary": {"rx_bytes_per_sec": 1000, "tx_bytes_per_sec": 2000,
                            "unexpected": "discard"},
        "network_interfaces": [{"name": "eth0", "is_uplink": True, "rx_rate_mbps": 0.1,
                                "private_value": "discard"}],
        "disks": [{"mountpoint": "/data"}],
    }, "host-id", at)

    assert summary["metrics"]["cpu"] == 24.5
    assert metrics["net_rx"] == 1000
    assert summary["network_summary"] == {"rx_bytes_per_sec": 1000, "tx_bytes_per_sec": 2000}
    assert summary["network_interfaces"] == [{"name": "eth0", "is_uplink": True, "rx_rate_mbps": 0.1}]
