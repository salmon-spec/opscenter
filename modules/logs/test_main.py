from fastapi.testclient import TestClient

import main


client = TestClient(main.app)
HOST_ID = "00000000-0000-0000-0000-000000000001"


def test_summary_reports_loki_readiness(monkeypatch):
    class Ready:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): return False

    monkeypatch.setattr(main, "LOKI_URL", "http://loki:3100")
    monkeypatch.setattr(main, "urlopen", lambda *args, **kwargs: Ready())
    monkeypatch.setattr(main, "_coverage", lambda: [])
    summary = client.get("/api/v1/summary").json()
    assert summary["status"] == "ok"
    assert summary["data"] == {"loki_configured": True, "loki_ready": True, "hosts_total": 0, "collectors_running": 0, "collectors_missing": 0, "collectors_fresh": 0, "collectors_stale": 0}


def test_running_collector_without_recent_ingestion_is_degraded(monkeypatch):
    monkeypatch.setattr(main, "_coverage", lambda: [{"id": "one", "name": "one", "log_agent_status": "running", "ingestion_fresh": False}])
    summary = main._summary()
    assert summary["status"] == "degraded" and summary["data"]["collectors_stale"] == 1
    assert "10分钟" in main._findings()["items"][0]["summary"]


def test_coverage_combines_active_assets_with_actual_loki_counts(monkeypatch, tmp_path):
    token = tmp_path / "token"
    token.write_text("test-only")
    monkeypatch.setenv("HOST_INVENTORY_INTERNAL_TOKEN_FILE", str(token))
    monkeypatch.setattr(main, "_request", lambda *args, **kwargs: {"status": "ok", "items": [{"id": "one"}, {"id": "two"}]})
    monkeypatch.setattr(main, "_loki", lambda *args: {"data": {"result": [{"metric": {"server_id": "one"}, "value": [0, "3"]}]}})
    assert main._coverage() == [{"id": "one", "ingestion_fresh": True}, {"id": "two", "ingestion_fresh": False}]


def test_missing_collectors_appear_in_summary_and_findings(monkeypatch):
    class Ready:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): return False
    monkeypatch.setattr(main, "LOKI_URL", "http://loki:3100")
    monkeypatch.setattr(main, "urlopen", lambda *args, **kwargs: Ready())
    monkeypatch.setattr(main, "_coverage", lambda: [{"id": "one", "name": "one", "log_agent_status": "running", "ingestion_fresh": True},
                                                 {"id": "two", "name": "two", "log_agent_status": "unknown"}])
    summary = client.get("/api/v1/summary").json()
    findings = client.get("/api/v1/findings").json()
    assert summary["status"] == "degraded" and summary["data"]["collectors_missing"] == 1
    assert [item["resource_id"] for item in findings["items"]] == ["two"]


def test_status_is_read_directly_from_loki(monkeypatch):
    class Ready:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): return False

    monkeypatch.setattr(main, "LOKI_URL", "http://loki:3100")
    monkeypatch.setattr(main, "urlopen", lambda *args, **kwargs: Ready())
    response = client.get("/api/v1/status")
    assert response.json()["ready"] is True
    assert response.json()["retention_days"] == main.RETENTION_DAYS


def test_health_preserves_loki_index_stats_shape(monkeypatch):
    monkeypatch.setattr(main, "LOKI_URL", "http://loki:3100")
    monkeypatch.setattr(main, "LOKI_DATA_DIR", "")
    monkeypatch.setattr(main, "_loki", lambda *args: {"streams": 2, "chunks": 3, "bytes": 4096, "entries": 7})
    monkeypatch.setattr(main, "_request", lambda *args, **kwargs: "")
    result = client.get("/api/v1/health").json()
    assert result["index_24h"] == {"streams": 2, "chunks": 3, "bytes": 4096, "entries": 7}


def test_query_is_bounded_host_scoped_and_direct_to_loki(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "LOKI_URL", "http://loki:3100")
    monkeypatch.setattr(main, "_hosts", lambda authorization=None: [{"id": HOST_ID, "name": "test-host"}])
    monkeypatch.setattr(main, "_loki", lambda path, params=None: calls.append((path, params)) or {"data": {"result": []}})
    response = client.get(f"/api/v1/hosts/{HOST_ID}/query?limit=5001")
    assert response.status_code == 422
    response = client.get(f"/api/v1/hosts/{HOST_ID}/query?limit=20", headers={"Authorization": "Bearer user-token"})
    assert response.status_code == 200
    assert response.json()["server_name"] == "test-host"
    assert calls[0][0] == "/loki/api/v1/query_range"
    assert calls[0][1]["limit"] == 20
    assert calls[0][1]["query"] == f'{{server_id="{HOST_ID}"}}'


def test_unknown_host_is_rejected_before_loki(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_hosts", lambda authorization=None: [])
    monkeypatch.setattr(main, "_loki", lambda *args: calls.append(args))
    response = client.get(f"/api/v1/hosts/{HOST_ID}/query")
    assert response.status_code == 404
    assert calls == []


def test_selector_escapes_search_and_rejects_invalid_source():
    host_id = main.UUID(HOST_ID)
    assert main._selector(host_id, "all", None, 'x"\\y') == f'{{server_id="{HOST_ID}"}} |= "x\\"\\\\y"'
    try:
        main._selector(host_id, "arbitrary", None, None)
    except Exception as error:
        assert getattr(error, "status_code", None) == 400
    else:
        raise AssertionError("invalid source was accepted")


def test_agent_overview_redacts_host_credentials_and_probes_loki(monkeypatch):
    monkeypatch.setattr(main, "LOKI_URL", "http://loki:3100")
    monkeypatch.setattr(main, "_hosts", lambda authorization=None: [{
        "id": HOST_ID, "name": "host", "host": "host.local", "log_agent_status": "running",
        "log_agent_version": "1.2", "ssh_key": "must-not-leak",
    }])
    monkeypatch.setattr(main, "_loki", lambda *args: {"data": {"result": [{"values": [[str(int(__import__('time').time() * 1e9)), "x"]]}]}})
    response = client.get("/api/v1/agents/overview?probe=true", headers={"Authorization": "Bearer test"})
    assert response.status_code == 200
    row = response.json()["agents"][0]
    assert row["ingestion_status"] == "fresh"
    assert row["diagnostic"] == "采集正常"
    assert "ssh_key" not in row
