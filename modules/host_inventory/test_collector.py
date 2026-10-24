import json
from datetime import datetime, timezone
from urllib.request import Request
from unittest.mock import MagicMock

import collector
from collector import HostInventoryReader
from fastapi.testclient import TestClient
from main import app
from storage import HostInventoryStore


def test_module_version_matches_release():
    assert TestClient(app).get("/api/v1/version").json()["version"] == "0.2.7"


def test_internal_asset_ids_and_log_coverage_are_token_scoped(monkeypatch, tmp_path):
    token = tmp_path / 'token'; token.write_text('internal-test')
    monkeypatch.setenv('OPS_MODULE_INTERNAL_TOKEN_FILE', str(token))
    monkeypatch.setenv('HOST_INVENTORY_INGEST_TOKEN_FILE', str(token))
    monkeypatch.setattr('main.hosts_provider', lambda: {'status':'ok','items':[
        {'id':'active-id','name':'active','log_agent_status':'unknown','ssh_key':'must-not-leak'}]})
    client = TestClient(app)
    for suffix in ['active-hosts','log-coverage']:
        assert client.get('/api/v1/internal/'+suffix).status_code == 401
        result = client.get('/api/v1/internal/'+suffix,headers={'X-OpsCenter-Module-Token':'internal-test'})
        assert result.status_code == 200 and 'must-not-leak' not in result.text
    assert result.json()['items'][0] == {'id':'active-id','name':'active','log_agent_status':'unknown'}


def test_internal_hosts_require_token_and_redact_secret_fields(monkeypatch, tmp_path):
    token = tmp_path / "token"
    token.write_text("inventory-test", encoding="utf-8")
    monkeypatch.setenv("OPS_MODULE_INTERNAL_TOKEN_FILE", str(token))
    monkeypatch.setattr("main.hosts_provider", lambda: {"status": "ok", "items": [{
        "id": "host-1", "name": "node", "status": "online", "ssh_key": "private", "remark": "note",
    }]})
    client = TestClient(app)
    assert client.get("/api/v1/internal/hosts").status_code == 401
    response = client.get("/api/v1/internal/hosts", headers={"X-OpsCenter-Module-Token": "inventory-test"})
    assert response.status_code == 200
    row = response.json()["items"][0]
    assert row["id"] == "host-1" and row["status"] == "online"
    assert "ssh_key" not in row and "remark" not in row


class FakeStore:
    def __init__(self, hosts=None, state=None):
        self.hosts = hosts or []
        self.state = state

    def ensure_schema(self):
        pass

    def save_snapshot(self, hosts, collected_at):
        self.hosts = [{**host, "source_seen": True} for host in hosts]
        self.state = {"status": "ok", "collected_at": collected_at, "error": None}

    def record_failure(self, collected_at):
        self.state = {"status": "degraded", "collected_at": collected_at, "error": "主机数据源暂不可用"}

    def read_snapshot(self):
        return self.hosts, self.state


def test_collect_filters_secrets_and_reports_hosts(monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    source = [{
        "id": "host-1", "name": "node-1", "host": "10.0.0.1", "status": "offline",
        "agent_status": "error", "ssh_user": "root", "has_credentials": True,
        "last_error": "private detail", "last_seen": now, "lan_ip": "10.0.0.1",
        "tags": ["k3s"], "ssh_port": 2222, "remark": "visible note", "agent_version": "2.6.0",
    }]

    def open_source(request: Request, timeout: float):
        assert request.full_url == "http://backend/api/v2/servers"
        assert timeout == 5

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def read(self):
                return json.dumps(source).encode()

        return Response()

    monkeypatch.setenv("HOST_INVENTORY_SOURCE_URL", "http://backend/api/v2/servers")
    monkeypatch.setattr("collector.urlopen", open_source)
    monkeypatch.setattr(collector, "_last_sync", 0)

    summary, findings, hosts = HostInventoryReader(FakeStore()).collect()

    assert summary["status"] == "degraded"
    assert summary["data"] == {
        "hosts_total": 1, "hosts_online": 0, "hosts_offline": 1,
        "hosts_unknown": 0, "agent_running": 0, "source_missing": 0,
        "cache_age_seconds": 0,
    }
    assert findings[0]["resource_id"] == "host-1"
    assert hosts[0]["host"] == "10.0.0.1"
    assert hosts[0]["tags"] == '["k3s"]'
    assert hosts[0]["ssh_port"] == 2222 and hosts[0]["remark"] == "visible note"
    assert "ssh_user" not in hosts[0] and "has_credentials" not in hosts[0]
    assert "last_error" not in hosts[0]


def test_source_failure_keeps_last_snapshot(monkeypatch):
    store = FakeStore(
        hosts=[{"id": "host-1", "name": "node-1", "address": "10.0.0.1", "status": "online",
                "agent_status": "running", "source_seen": True}],
        state={"status": "ok", "collected_at": datetime.now(timezone.utc).isoformat(), "error": None},
    )
    monkeypatch.setattr(collector, "_last_sync", 0)
    monkeypatch.setattr(HostInventoryReader, "fetch_source", lambda self: (_ for _ in ()).throw(RuntimeError("secret")))

    summary, _, hosts = HostInventoryReader(store).collect()

    assert summary["status"] == "degraded"
    assert hosts[0]["id"] == "host-1"
    assert summary["error"] == "主机数据源暂不可用"
    assert "secret" not in str(summary)


def test_disabled_sync_keeps_module_database_as_read_source(monkeypatch):
    store = FakeStore(
        hosts=[{"id": "host-1", "name": "node-1", "status": "online", "agent_status": "running", "source_seen": True, "last_seen": None}],
        state={"status": "ok", "collected_at": datetime.now(timezone.utc).isoformat(), "error": None},
    )
    monkeypatch.setenv("HOST_INVENTORY_SYNC_ENABLED", "false")
    monkeypatch.setattr(HostInventoryReader, "fetch_source", lambda self: (_ for _ in ()).throw(AssertionError("legacy source called")))

    summary, _, hosts = HostInventoryReader(store).collect()

    assert summary["status"] == "ok"
    assert hosts[0]["id"] == "host-1"


def test_stale_snapshot_is_degraded_and_kept_readable(monkeypatch):
    store = FakeStore(
        hosts=[{"id": "host-1", "name": "node-1", "status": "online", "agent_status": "running", "source_seen": True}],
        state={"status": "ok", "collected_at": "2026-10-12T00:00:00+00:00", "error": None},
    )
    monkeypatch.setenv("HOST_INVENTORY_SYNC_ENABLED", "false")
    monkeypatch.setattr(HostInventoryReader, "fetch_source", lambda self: (_ for _ in ()).throw(AssertionError("legacy source called")))

    summary, findings, hosts = HostInventoryReader(store).collect()

    assert summary["status"] == "degraded"
    assert summary["error"] == "主机健康状态超过 300 秒未更新，当前展示最后成功数据"
    assert summary["data"]["cache_age_seconds"] > 300
    assert hosts[0]["id"] == "host-1"
    assert findings[0]["summary"] == "健康状态来源超过 300 秒未更新"


def test_recent_module_owned_telemetry_replaces_stale_import_timestamp(monkeypatch):
    recent = datetime.now(timezone.utc)
    store = FakeStore(
        hosts=[{
            "id": "host-1", "name": "node-1", "status": "online", "agent_status": "running",
            "source_seen": True, "telemetry_updated_at": recent, "last_seen": recent.isoformat(),
        }],
        state={"status": "ok", "collected_at": "2026-10-12T00:00:00+00:00", "error": None},
    )
    monkeypatch.setenv("HOST_INVENTORY_SYNC_ENABLED", "false")

    summary, findings, _ = HostInventoryReader(store).collect()

    assert summary["status"] == "ok"
    assert summary["data"]["cache_age_seconds"] < 300
    assert findings == []


def test_snapshot_write_uses_transactional_cursor_batch():
    connection = MagicMock()
    store = HostInventoryStore()
    store.connect = lambda: connection
    host = {"id": "host-1", "name": "node-1", "address": "10.0.0.1"}

    store.save_snapshot([host], "2026-10-12T00:00:00+00:00")

    cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.execute.assert_called_once_with("UPDATE hosts SET source_seen = FALSE")
    cursor.executemany.assert_called_once()
    connection.__enter__.return_value.execute.assert_called_once()


def test_hosts_read_endpoint_uses_the_module_contract(monkeypatch):
    monkeypatch.setattr(HostInventoryReader, "collect", lambda self: ({
        "status": "ok", "collected_at": datetime.now(timezone.utc).isoformat(), "error": None,
    }, [], [{"id": "host-1", "name": "node-1", "address": "10.0.0.1"}]))

    client = TestClient(app)
    assert client.get("/api/v1/module").json()["key"] == "host-inventory"
    assert client.get("/api/v1/summary").status_code == 200
    assert client.get("/api/v1/hosts").json()["items"][0]["id"] == "host-1"
    assert client.post("/api/v1/hosts").status_code == 401


def test_hosts_endpoint_hides_source_errors(monkeypatch):
    def fail(self):
        raise RuntimeError("private source details")

    monkeypatch.setattr(HostInventoryReader, "collect", fail)
    response = TestClient(app).get("/api/v1/hosts")
    assert response.json()["status"] == "unavailable"
    assert "private source details" not in response.text


def test_agent_telemetry_is_machine_authenticated_and_module_owned(monkeypatch, tmp_path):
    token_file = tmp_path / "module-token"
    token_file.write_text("test-secret", encoding="utf-8")
    monkeypatch.setenv("HOST_INVENTORY_INGEST_TOKEN_FILE", str(token_file))
    received = []

    class Store:
        def update_agent_telemetry(self, data):
            received.append(data)
            return True

    monkeypatch.setattr("main.HostInventoryStore", Store)
    client = TestClient(app)
    payload = {
        "id": "00000000-0000-0000-0000-000000000001", "status": "online",
        "agent_status": "running", "agent_type": "remote", "agent_version": "2.6.0",
    }
    assert client.post("/api/v1/agent/telemetry", json=payload).status_code == 401
    response = client.post(
        "/api/v1/agent/telemetry", json=payload,
        headers={"X-OpsCenter-Module-Token": "test-secret"},
    )
    assert response.status_code == 204
    assert received[0]["id"] == payload["id"]
    assert received[0]["agent_status"] == "running"


def test_telemetry_update_only_changes_runtime_fields():
    connection = MagicMock()
    store = HostInventoryStore()
    store.connect = lambda: connection
    payload = {
        "id": "host-1", "status": "online", "docker_available": True, "is_local": False,
        "agent_status": "running", "agent_type": "remote", "last_seen": None,
        "agent_version": "2.6.0", "log_agent_status": "running", "log_agent_version": "1.2.0",
    }

    assert store.update_agent_telemetry(payload) is True
    statement = connection.__enter__.return_value.execute.call_args.args[0]
    assert "agent_status=%s" in statement and "telemetry_updated_at=now()" in statement
    assert "name=" not in statement and "host=" not in statement and "tags=" not in statement


def test_host_create_requires_workbench_login():
    response = TestClient(app).post("/api/v1/hosts", json={"name": "host", "host": "10.0.0.1"})
    assert response.status_code == 401


def test_host_create_rejects_read_only_module_user(monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, app.state.current_user, lambda: {"username": "reader", "role": "readonly"})
    response = TestClient(app).post("/api/v1/hosts", json={"name": "host", "host": "10.0.0.1"})
    assert response.status_code == 403
    monkeypatch.delitem(app.dependency_overrides, app.state.current_user)


def test_host_create_registers_management_then_writes_module_store(monkeypatch):
    registered = []
    monkeypatch.setattr("main.register_management_host", lambda *args: registered.append(args))
    saved = []
    class Store:
        def create_host(self, payload, actor):
            saved.append((payload, actor))
    monkeypatch.setattr("main.HostInventoryStore", Store)
    monkeypatch.setitem(app.dependency_overrides, app.state.require_editor, lambda: {"username": "alice", "role": "editor"})
    client = TestClient(app)
    response = client.post("/api/v1/hosts", json={"name": " node ", "host": "10.0.0.2", "tags": [" k3s ", ""]})
    assert response.status_code == 201
    assert response.json()["id"] == saved[0][0]["id"]
    assert registered[0][0] == saved[0][0]["id"]
    assert saved[0][0]["tags"] == ["k3s"] and saved[0][1] == "alice"
    monkeypatch.delitem(app.dependency_overrides, app.state.require_editor)


def test_host_update_and_archive_use_module_store(monkeypatch):
    monkeypatch.setattr("main.register_management_host", lambda *args: None)
    calls = []
    class Store:
        def update_host(self, host_id, values, actor):
            calls.append(("update", host_id, values, actor))
            return True
        def archive_host(self, host_id, actor):
            calls.append(("archive", host_id, actor))
            return True
    monkeypatch.setattr("main.HostInventoryStore", Store)
    monkeypatch.setitem(app.dependency_overrides, app.state.require_editor, lambda: {"username": "admin", "role": "admin"})
    client = TestClient(app)
    host_id = "59a3d229-25b4-4bf0-bc2d-9f7dbb9dfb42"
    assert client.put(f"/api/v1/hosts/{host_id}", json={"name": "node", "host": "10.0.0.1"}).status_code == 200
    assert client.post(f"/api/v1/hosts/{host_id}/archive").status_code == 200
    assert [call[0] for call in calls] == ["update", "archive"]
    assert calls[0][3] == "admin"
    monkeypatch.delitem(app.dependency_overrides, app.state.require_editor)


def test_archive_host_types_jsonb_audit_name_parameter(monkeypatch):
    calls = []

    class Database:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params):
            calls.append((sql, params))
            return MagicMock(fetchone=lambda: {"name": "node"}) if "UPDATE hosts" in sql else None

    store = HostInventoryStore()
    monkeypatch.setattr(store, "connect", Database)
    assert store.archive_host("host-id", "admin")
    assert "jsonb_build_object('name',%s::text)" in calls[1][0]
    assert calls[1][1] == ("admin", "host-id", "node")
