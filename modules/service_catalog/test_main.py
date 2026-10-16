from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
from main import app


def test_module_version_matches_release():
    assert TestClient(app).get("/api/v1/version").json()["version"] == "0.7.2"


class FakeStore:
    def __init__(self, snapshots=None):
        self.snapshots = snapshots or {}
        self.overrides = {}
        self.profile_updates = []
        self.expired = []
        self.visibility_updates = []

    def read_snapshot(self, key):
        return self.snapshots.get(key)

    def save_snapshot(self, key, data, collected_at):
        self.snapshots[key] = (data, collected_at)

    def ping(self):
        return None

    def list_manual_services(self):
        return []

    def credential_counts(self):
        return {}

    def preferences(self):
        return {}

    def profile_overrides(self):
        return self.overrides

    def update_profile(self, key, values, actor):
        self.profile_updates.append((key, values, actor))

    def expire_snapshot(self, key):
        self.expired.append(key)

    def set_visibility(self, key, hidden, actor):
        self.visibility_updates.append((key, hidden, actor))

    def list_credentials(self, key):
        return []

    def credential_history(self, key, limit):
        return []

    def probe_history(self, key, hours=24, limit=20):
        return []

    def probe_summaries(self, hours=24):
        return {}

    def save_probe_result(self, key, result):
        pass


def test_catalog_reads_from_module_snapshot_without_backend(monkeypatch):
    calls = []
    store = FakeStore({"services": ([], datetime.now(timezone.utc))})
    monkeypatch.setattr(main, "CatalogStore", lambda: store)
    monkeypatch.setattr(main, "get", lambda path, query=None: calls.append((path, query)) or [])
    client = TestClient(app)
    assert client.get("/api/v1/services").json() == []
    overview = client.get("/api/v1/health-overview?hours=24").json()
    assert overview["summary"]["total"] == 0
    assert client.get("/api/v1/hidden").json() == []
    assert calls == []


def test_refresh_source_snapshot_updates_module_owned_cache(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(main, "CatalogStore", lambda: store)
    monkeypatch.setattr(main, "get", lambda path: [{"key": "web", "status": "up"}] if path == "/services/plaza" else None)

    main.refresh_source_snapshot()

    rows, collected_at = store.snapshots["services"]
    assert rows == [{"key": "web", "status": "up"}]
    assert (datetime.now(timezone.utc) - collected_at).total_seconds() < 1


def test_health_overview_uses_module_owned_service_snapshot(monkeypatch):
    now = datetime.now(timezone.utc)
    services = [
        {"key": "web", "status": "up", "uptime_percent": 99.5, "status_checked_at": now.isoformat()},
        {"key": "db", "status": "down"},
        {"key": "paused", "status": "up", "probe_enabled": False},
    ]
    monkeypatch.setattr(main, "CatalogStore", lambda: FakeStore({"services": (services, now)}))
    monkeypatch.setattr(main, "get", lambda *args, **kwargs: pytest.fail("legacy backend must not be called"))
    overview = TestClient(app).get("/api/v1/health-overview").json()
    assert overview["summary"]["total"] == 3
    assert overview["summary"]["up"] == 1
    assert overview["summary"]["down"] == 1
    assert overview["summary"]["disabled"] == 1
    assert overview["summary"]["average_uptime_percent"] == 99.5
    assert {item["key"]: item["status"] for item in overview["items"]} == {
        "web": "up", "db": "down", "paused": "disabled",
    }


def test_service_freshness_recalculates_cached_age(monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    store = FakeStore({"services": ([
        {"key": "stale", "status": "up", "status_checked_at": old, "status_age_seconds": 0},
        {"key": "disabled", "status": "disabled", "probe_enabled": False, "status_age_seconds": 0},
    ], datetime.now(timezone.utc))})
    monkeypatch.setattr(main, "CatalogStore", lambda: store)
    services, _ = main.services_snapshot()
    by_key = {row["key"]: row for row in services}
    assert by_key["stale"]["status_age_seconds"] >= 1200
    assert by_key["stale"]["stale"] is True
    assert by_key["disabled"]["stale"] is False


def test_health_overview_preserves_imported_uptime_snapshot(monkeypatch):
    now = datetime.now(timezone.utc)
    imported_at = now - timedelta(minutes=20)
    store = FakeStore({
        "services": ([{"key": "web", "status": "up", "status_checked_at": imported_at.isoformat()}], now),
        "health-overview:24": ({
            "data_timestamp": imported_at.timestamp(),
            "summary": {"average_uptime_percent": 98.25},
            "items": [{"key": "web", "uptime_percent": 98.25}],
        }, imported_at),
    })
    monkeypatch.setattr(main, "CatalogStore", lambda: store)
    overview = TestClient(app).get("/api/v1/health-overview?hours=24").json()
    assert overview["summary"]["average_uptime_percent"] == 98.25
    assert overview["stale"] is True


def test_health_overview_prefers_module_owned_probe(monkeypatch):
    checked = datetime.now(timezone.utc)
    class Store(FakeStore):
        def probe_summaries(self, hours=24):
            return {"web": {"status": "down", "checked_at": checked, "checks": 1,
                             "first_checked_at": checked, "uptime_percent": 0, "avg_latency_ms": 4000}}
    imported_at = checked - timedelta(minutes=20)
    monkeypatch.setattr(main, "CatalogStore", lambda: Store({
        "services": ([{"key": "web", "status": "up", "status_checked_at": (checked - timedelta(hours=1)).isoformat()}], checked),
        "health-overview:24": ({"items": [{"key": "web", "uptime_percent": 98.25}]}, imported_at),
    }))

    overview = TestClient(app).get("/api/v1/health-overview").json()

    assert overview["items"][0]["status"] == "down"
    assert overview["items"][0]["checks"] == 1
    assert overview["items"][0]["stale"] is False
    assert overview["items"][0]["uptime_percent"] == 98.25
    assert overview["stale"] is True


def test_health_overview_validates_range():
    response = TestClient(app).get("/api/v1/health-overview?hours=0")
    assert response.status_code == 400


def test_summary_and_findings_come_from_cached_module_data(monkeypatch):
    now = datetime.now(timezone.utc)
    store = FakeStore({"services": ([
        {"id": "plaza:broken", "key": "broken", "service_id": "svc-1", "name": "Broken", "status": "down", "stale": True},
    ], now)})
    monkeypatch.setattr(main, "CatalogStore", lambda: store)
    client = TestClient(app)
    summary = client.get("/api/v1/summary").json()
    findings = client.get("/api/v1/findings").json()
    assert summary["data"]["services_total"] == 1
    assert findings["items"][0]["id"] == "service:broken:health"
    assert findings["items"][0]["severity"] == "critical"


def test_catalog_snapshot_is_read_without_contacting_legacy_source(monkeypatch):
    old_time = datetime.now(timezone.utc) - timedelta(minutes=2)
    store = FakeStore({"services": ([{"id": "plaza:a"}], old_time)})
    monkeypatch.setattr(main, "CatalogStore", lambda: store)
    monkeypatch.setattr(main, "get", lambda *args, **kwargs: pytest.fail("legacy source must not be called"))
    data, collected_at = main.services_snapshot()
    assert data == [{"id": "plaza:a", "credential_count": 0, "has_credentials": False}]
    assert collected_at == old_time


def test_hidden_catalog_items_can_be_restored_from_module_preferences(monkeypatch):
    key = "wiki"
    store = FakeStore({"services": ([{"id": "plaza:wiki", "key": key}], datetime.now(timezone.utc))})
    store.preferences = lambda: {key: {"hidden": True}}
    monkeypatch.setattr(main, "CatalogStore", lambda: store)
    monkeypatch.setitem(app.dependency_overrides, app.state.require_editor, lambda: {"username": "alice"})

    client = TestClient(app)
    assert client.get("/api/v1/hidden").json()[0]["key"] == key
    response = client.put(f"/api/v1/services/{key}/visibility", json={"hidden": False})
    monkeypatch.delitem(app.dependency_overrides, app.state.require_editor)

    assert response.status_code == 200
    assert store.visibility_updates == [(key, False, "alice")]


def test_service_list_applies_module_owned_profile_overrides(monkeypatch):
    store = FakeStore({"services": ([
        {"id": "plaza:wiki", "key": "wiki", "name": "Old", "entry_url": "http://old.local"},
    ], datetime.now(timezone.utc))})
    store.overrides["wiki"] = {
        "name": "Wiki", "entry_url": "http://wiki.local", "probe_enabled": False,
        "profile_updated_at": "2026-10-13T00:00:00+00:00",
    }
    monkeypatch.setattr(main, "CatalogStore", lambda: store)

    services, _ = main.services_snapshot()

    assert services[0]["name"] == "Wiki"
    assert services[0]["url"] == "http://wiki.local"
    assert services[0]["probe_enabled"] is False
    assert services[0]["profile_updated_at"] == "2026-10-13T00:00:00+00:00"


def test_service_profile_edit_is_owned_and_audited_by_module_store(monkeypatch):
    key = "wiki"
    store = FakeStore({"services": ([{"id": "plaza:wiki", "key": key, "name": "Wiki"}], datetime.now(timezone.utc))})
    monkeypatch.setattr(main, "CatalogStore", lambda: store)
    monkeypatch.setitem(app.dependency_overrides, app.state.require_editor, lambda: {"username": "alice", "role": "editor"})
    response = TestClient(app).put(f"/api/v1/services/plaza/{key}", json={
        "server_id": "59a3d229-25b4-4bf0-bc2d-9f7dbb9dfb42", "server_name": "node", "server_host": "10.0.0.5",
        "name": "Wiki", "entry_url": "http://wiki.local", "health_url": "", "category": "应用",
        "tags": ["ops", "ops", "wiki"], "probe_enabled": False,
        "probe_success_statuses": "200-399, 401,403",
    })
    monkeypatch.delitem(app.dependency_overrides, app.state.require_editor)

    assert response.status_code == 200
    key, values, actor = store.profile_updates[0]
    assert key == "wiki" and actor == "alice"
    assert values["server_id"] == "59a3d229-25b4-4bf0-bc2d-9f7dbb9dfb42"
    assert values["tags"] == ["ops", "wiki"]
    assert values["probe_success_statuses"] == "200-399,401,403"


def test_service_profile_rejects_invalid_health_policy(monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, app.state.require_editor, lambda: {"username": "admin", "role": "admin"})
    response = TestClient(app).put("/api/v1/services/plaza/wiki", json={"probe_interval_seconds": 5})
    monkeypatch.delitem(app.dependency_overrides, app.state.require_editor)
    assert response.status_code == 422


def test_readiness_requires_module_database(monkeypatch):
    called = []
    store = FakeStore({"services": ([], datetime.now(timezone.utc))})
    store.ping = lambda: called.append(True)
    monkeypatch.setattr(main, "CatalogStore", lambda: store)
    assert TestClient(app).get("/health/ready").status_code == 200
    assert called == [True]


def test_readiness_stays_down_until_snapshot_imported(monkeypatch):
    store = FakeStore()
    store.ping = lambda: None
    monkeypatch.setattr(main, "CatalogStore", lambda: store)
    assert TestClient(app).get("/health/ready").status_code == 503


def test_source_error_is_sanitized(monkeypatch):
    monkeypatch.setattr(main, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(URLError("secret")))
    with pytest.raises(HTTPException) as error:
        main.get("/services/plaza")
    assert error.value.status_code == 502
    assert "secret" not in error.value.detail


def test_service_create_requires_workbench_login():
    response = TestClient(app).post("/api/v1/services", json={
        "server_id": "host-1", "server_name": "host", "name": "wiki", "url": "http://wiki.local",
    })
    assert response.status_code == 401


def test_scan_route_forwards_global_session_through_module_api(monkeypatch):
    observed = {}
    store = FakeStore()

    def open_source(request, timeout):
        if request.full_url.endswith("/scan"):
            observed["url"] = request.full_url
            observed["method"] = request.get_method()
            observed["authorization"] = request.get_header("Authorization")
            observed["timeout"] = timeout
            body = b'{"added":1,"updated":2,"servers":[]}'
        else:
            body = b"[]"

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def read(self):
                return body

        return Response()

    monkeypatch.setenv("SERVICE_CATALOG_SOURCE_URL", "http://backend:9091/api/v2")
    monkeypatch.setenv("SERVICE_CATALOG_SCAN_TIMEOUT_SECONDS", "90")
    monkeypatch.setattr(main, "CatalogStore", lambda: store)
    monkeypatch.setattr(main, "urlopen", open_source)
    monkeypatch.setitem(app.dependency_overrides, app.state.require_editor, lambda: {"username": "admin", "role": "admin"})
    response = TestClient(app).post("/api/v1/scan", headers={"Authorization": "Bearer session"})
    monkeypatch.delitem(app.dependency_overrides, app.state.require_editor)

    assert response.status_code == 200
    assert response.json()["added"] == 1
    assert observed == {
        "url": "http://backend:9091/api/v2/scan",
        "method": "POST",
        "authorization": "Bearer session",
        "timeout": 90,
    }
    assert store.expired == []
    assert store.snapshots["services"][0] == []


def test_scan_route_requires_workbench_token():
    assert TestClient(app).post("/api/v1/scan").status_code == 401


def test_scan_route_sanitizes_source_errors(monkeypatch):
    monkeypatch.setattr("main.urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(URLError("private source detail")))
    monkeypatch.setitem(app.dependency_overrides, app.state.require_editor, lambda: {"username": "admin", "role": "admin"})
    response = TestClient(app).post("/api/v1/scan", headers={"Authorization": "Bearer session"})
    monkeypatch.delitem(app.dependency_overrides, app.state.require_editor)

    assert response.status_code == 502
    assert "private source detail" not in response.text


def test_service_detail_uses_module_snapshot_and_profile(monkeypatch):
    store = FakeStore({"services": ([{
        "key": "catalog key", "id": "plaza:catalog key", "name": "Old service",
        "entry_url": "http://old.local", "server_id": "host-1", "server_name": "VM1",
        "server_host": "192.0.2.1", "probe_enabled": True,
    }], datetime.now(timezone.utc)), "health-overview:24": ({
        "items": [{"key": "catalog key", "checks": 24, "uptime_percent": 98.5, "avg_latency_ms": 42.0}],
    }, datetime.now(timezone.utc))})
    store.overrides["catalog key"] = {
        "name": "New service", "entry_url": "http://new.local", "probe_enabled": False,
        "profile_updated_at": "2026-10-13T00:00:00+00:00",
    }
    monkeypatch.setattr(main, "CatalogStore", lambda: store)
    monkeypatch.setattr(main, "forward_request", lambda *args, **kwargs: pytest.fail("legacy detail API must not be called"))
    monkeypatch.setitem(app.dependency_overrides, app.state.current_user, lambda: {"username": "admin", "role": "admin"})
    response = TestClient(app).get(
        "/api/v1/services/plaza/catalog%20key/detail",
    )
    monkeypatch.delitem(app.dependency_overrides, app.state.current_user)

    assert response.status_code == 200
    assert response.json()["name"] == "New service"
    assert response.json()["entry_url"] == "http://new.local"
    assert response.json()["credentials"] == []
    assert response.json()["credential_count"] == 0
    assert response.json()["probe_policy"]["enabled"] is False
    assert response.json()["server"] == {"id": "host-1", "name": "VM1", "host": "192.0.2.1"}
    assert response.json()["probe_summary"]["uptime_percent_24h"] == 98.5


def test_service_probe_is_saved_by_module_and_exposed_in_detail(monkeypatch):
    now = datetime.now(timezone.utc)
    saved = []
    class Store(FakeStore):
        def save_probe_result(self, key, result):
            saved.append((key, result))
        def probe_history(self, key, hours=24, limit=20):
            return [{"id": "probe-1", "status": "up", "http_status": 200,
                     "latency_ms": 23, "checked_at": now.isoformat(), "error": ""}] if saved else []
    store = Store({"services": ([{"key": "wiki", "name": "Wiki", "url": "http://wiki.local"}], now)})
    monkeypatch.setattr(main, "CatalogStore", lambda: store)
    monkeypatch.setattr(main, "probe_service", lambda service: {
        "status": "up", "http_status": 200, "latency_ms": 23, "error": "",
        "probe_url": service["url"], "checked_at": now,
    })
    monkeypatch.setattr(main, "forward_request", lambda *args, **kwargs: pytest.fail("probe must not call legacy backend"))
    monkeypatch.setitem(app.dependency_overrides, app.state.require_editor, lambda: {"username": "admin"})
    monkeypatch.setitem(app.dependency_overrides, app.state.current_user, lambda: {"username": "admin"})
    client = TestClient(app)

    probed = client.post("/api/v1/services/plaza/wiki/probe")
    detail = client.get("/api/v1/services/plaza/wiki/detail")
    monkeypatch.delitem(app.dependency_overrides, app.state.require_editor)
    monkeypatch.delitem(app.dependency_overrides, app.state.current_user)

    assert probed.status_code == 200 and saved[0][0] == "wiki"
    assert detail.json()["status"] == "up"
    assert detail.json()["recent_probes"][0]["http_status"] == 200
    assert detail.json()["probe_summary"]["checks_24h"] == 1


@pytest.mark.parametrize("verify_tls", [False, True, None])
def test_probe_honors_per_service_tls_policy(monkeypatch, verify_tls):
    contexts = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def fake_urlopen(_request, **kwargs):
        contexts.append(kwargs["context"])
        return Response()

    monkeypatch.setattr(main, "urlopen", fake_urlopen)
    result = main.probe_service({"health_url": "https://example.test/", "probe_verify_tls": verify_tls})

    assert result["status"] == "up"
    assert (contexts[0] is None) is (verify_tls is not False)
    if verify_tls is False:
        assert contexts[0].verify_mode == main.ssl.CERT_NONE


def test_probe_cycle_runs_only_services_due_for_module_owned_probe(monkeypatch):
    now = datetime.now(timezone.utc)
    rows = [
        {"key": "due", "url": "http://due.local", "status_checked_at": (now - timedelta(minutes=2)).isoformat()},
        {"key": "fresh", "url": "http://fresh.local", "status_checked_at": now.isoformat()},
        {"key": "disabled", "url": "http://disabled.local", "probe_enabled": False},
    ]
    saved = []
    class Store(FakeStore):
        def save_probe_result(self, key, result):
            saved.append(key)
    store = Store()
    monkeypatch.setattr(main, "services_snapshot", lambda include_hidden=False: (rows, now))
    monkeypatch.setattr(main, "CatalogStore", lambda: store)
    monkeypatch.setattr(main, "probe_service", lambda service: {
        "status": "up", "http_status": 200, "latency_ms": 10,
        "error": "", "probe_url": service["url"], "checked_at": now,
    })

    assert main.run_probe_cycle() == 1
    assert saved == ["due"]


def test_credential_reveal_is_module_owned_and_disables_caching(monkeypatch):
    class Store(FakeStore):
        def reveal_credential(self, key, credential_id, actor, ip):
            return {"id": credential_id, "username": "admin", "password": "example"}
    monkeypatch.setattr(main, "CatalogStore", lambda: Store({"services": ([{"key": "catalog-key"}], datetime.now(timezone.utc))}))
    monkeypatch.setitem(app.dependency_overrides, app.state.require_editor, lambda: {"username": "admin", "role": "admin"})
    response = TestClient(app).post(
        "/api/v1/services/plaza/catalog-key/credentials/59a3d229-25b4-4bf0-bc2d-9f7dbb9dfb42/reveal",
        headers={"Authorization": "Bearer session"},
    )
    monkeypatch.delitem(app.dependency_overrides, app.state.require_editor)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"


def test_credential_create_is_module_owned_and_audited(monkeypatch):
    saved = []
    class Store(FakeStore):
        def create_credential(self, key, values, actor):
            saved.append((key, values, actor))
            return {"id": "credential-1", "label": values["label"]}
    monkeypatch.setattr(main, "CatalogStore", lambda: Store({"services": ([{"key": "catalog-key"}], datetime.now(timezone.utc))}))
    monkeypatch.setitem(app.dependency_overrides, app.state.require_editor, lambda: {"username": "admin", "role": "admin"})
    response = TestClient(app).post(
        "/api/v1/services/plaza/catalog-key/credentials",
        headers={"Authorization": "Bearer session"},
        json={"label": "ops", "username": "admin", "password": "secret"},
    )
    monkeypatch.delitem(app.dependency_overrides, app.state.require_editor)

    assert response.status_code == 201
    assert response.json() == {"id": "credential-1", "label": "ops"}
    assert saved[0][0] == "catalog-key" and saved[0][1]["password"] == "secret" and saved[0][2] == "admin"


def test_credential_key_encrypts_without_storing_plaintext(monkeypatch, tmp_path):
    from cryptography.fernet import Fernet
    from storage import _credential_fernet

    key_path = tmp_path / "credential.key"
    key_path.write_bytes(Fernet.generate_key())
    monkeypatch.setenv("SERVICE_CATALOG_CREDENTIAL_KEY_FILE", str(key_path))
    cipher = _credential_fernet().encrypt(b"private-password")
    assert cipher != b"private-password"
    assert _credential_fernet().decrypt(cipher) == b"private-password"


def test_legacy_adapter_hides_upstream_server_errors(monkeypatch):
    def fail(*args, **kwargs):
        raise HTTPError("http://backend", 500, "failure", None, None)

    monkeypatch.setattr(main, "urlopen", fail)
    with pytest.raises(HTTPException) as error:
        main.forward_legacy("GET", "/services/plaza/example/detail", "Bearer session")

    assert error.value.status_code == 502
    assert "failure" not in error.value.detail


def test_manual_service_create_is_validated_and_auditable(monkeypatch):
    saved = []
    class Store:
        def create_manual_service(self, payload, actor):
            saved.append((payload, actor))
    monkeypatch.setattr(main, "CatalogStore", Store)
    monkeypatch.setitem(app.dependency_overrides, app.state.require_editor, lambda: {"username": "alice", "role": "editor"})
    response = TestClient(app).post("/api/v1/services", json={
        "server_id": "59a3d229-25b4-4bf0-bc2d-9f7dbb9dfb42", "server_name": "host",
        "name": "wiki", "url": "http://wiki.local", "health_path": "/health",
    })
    assert response.status_code == 201
    assert saved[0][0]["url"] == "http://wiki.local/" and saved[0][1] == "alice"
    monkeypatch.delitem(app.dependency_overrides, app.state.require_editor)


def test_manual_service_edit_and_archive_use_module_store(monkeypatch):
    calls = []
    class Store:
        def update_manual_service(self, service_id, values, actor):
            calls.append(("update", service_id, actor, values["name"]))
            return True
        def archive_manual_service(self, service_id, actor):
            calls.append(("archive", service_id, actor))
            return True
    monkeypatch.setattr(main, "CatalogStore", Store)
    monkeypatch.setitem(app.dependency_overrides, app.state.require_editor, lambda: {"username": "admin", "role": "admin"})
    client = TestClient(app)
    service_id = "59a3d229-25b4-4bf0-bc2d-9f7dbb9dfb42"
    payload = {"server_id": service_id, "server_name": "node", "name": "wiki", "url": "http://wiki.local"}
    assert client.put(f"/api/v1/services/manual/{service_id}", json=payload).status_code == 200
    assert client.post(f"/api/v1/services/manual/{service_id}/archive").status_code == 200
    assert calls == [("update", service_id, "admin", "wiki"), ("archive", service_id, "admin")]
    monkeypatch.delitem(app.dependency_overrides, app.state.require_editor)
