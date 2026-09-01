"""Curated service plaza contract tests (no network or database required)."""

from collections import Counter
from contextlib import contextmanager
from types import SimpleNamespace
import time

from app import plaza


def test_health_probe_is_stale_while_revalidate_and_never_blocks(monkeypatch):
    item = {"key": "fast-ui", "enabled": True, "health_url": "http://127.0.0.1/"}
    monkeypatch.setattr(plaza, "_cached_checks", {})
    monkeypatch.setattr(plaza, "_cached_at", 0.0)
    monkeypatch.setattr(plaza, "_refreshing", False)

    def slow_probe(_item):
        time.sleep(0.15)
        return {"status": "up", "http_status": 200, "latency_ms": 150, "health_error": ""}

    monkeypatch.setattr(plaza, "_probe", slow_probe)
    started = time.perf_counter()
    assert plaza._health_checks([item]) == {}
    assert time.perf_counter() - started < 0.08
    deadline = time.time() + 1
    while plaza._refreshing and time.time() < deadline:
        time.sleep(0.02)
    assert plaza._cached_checks["fast-ui"]["status"] == "up"


class _Query:
    def __init__(self, model):
        self.model = model

    def filter(self, *_args):
        return self

    def all(self):
        if self.model in (plaza.Service, plaza.PlazaServicePreference, plaza.PlazaServiceProfile):
            return []
        return [
            SimpleNamespace(id="server-vm1", host="10.66.66.4", name="虚拟-ubuntu"),
            SimpleNamespace(id="server-vm2", host="192.168.1.153", name="VM-2"),
            SimpleNamespace(id="server-vm3", host="10.66.66.6", name="虚拟机3 resolver"),
            SimpleNamespace(id="server-pve", host="10.66.66.3", name="主机2 PVE"),
        ]


class _Db:
    def query(self, model):
        return _Query(model)


@contextmanager
def _fake_db():
    yield _Db()


def test_catalog_contains_exactly_the_approved_web_services():
    catalog = plaza.load_catalog()
    assert len(catalog) == 19
    assert {item["key"] for item in catalog} == {
        "gitea", "gitlab", "jenkins", "nexus", "sonarqube",
        "dify", "ywjk", "opsbox", "ai-hub", "apollo-portal",
        "apollo-configservice", "token-monitor", "sanshengliubu",
        "grafana", "prometheus", "pve", "1panel", "it-tools", "stirling-pdf",
    }
    assert all(item["enabled"] for item in catalog)
    assert Counter(item["category"] for item in catalog) == {
        "代码与CI/CD": 5,
        "应用服务": 9,
        "监控与日志": 3,
        "安全与运维": 2,
    }
    by_key = {item["key"]: item for item in catalog}
    assert by_key["sonarqube"]["auth_mode"] == "local"
    assert by_key["sonarqube"]["entry_url"].endswith("/sessions/new")
    assert by_key["nexus"]["auth_mode"] == "local"
    assert all(item["auth_mode"] != "keycloak" for item in catalog)


def test_sensitive_entry_url_can_be_injected_without_committing_it(monkeypatch):
    monkeypatch.setenv("OPSCENTER_1PANEL_ENTRY_URL", "http://10.66.66.6:12110/private-entry")
    catalog = plaza.load_catalog()
    by_key = {item["key"]: item for item in catalog}
    assert by_key["1panel"]["entry_url"].endswith("/private-entry")


def test_plaza_response_never_contains_credentials(monkeypatch):
    monkeypatch.setattr(plaza, "get_db", _fake_db)
    monkeypatch.setattr(
        plaza,
        "_health_checks",
        lambda catalog: {
            item["key"]: {"status": "up", "http_status": 200, "latency_ms": 1, "health_error": ""}
            for item in catalog
        },
    )
    rows = plaza.list_plaza_services()
    assert len(rows) == 19
    assert all(row["status"] == "up" for row in rows)
    assert all("password" not in row and "account" not in row and "client_secret" not in row for row in rows)
    assert {row["auth_mode"] for row in rows} == {"none", "local"}


def test_manual_web_service_is_merged_without_credentials(monkeypatch):
    server = SimpleNamespace(id="server-vm3", host="10.66.66.6", name="虚拟机3 resolver")
    manual = SimpleNamespace(
        id="manual-1", server_id=server.id, source="manual", hidden=False,
        name="内部工具", url="http://192.168.1.154:8999/", health_path="/health",
        description="手动服务", category="应用服务", icon="tool",
        account="admin", password="must-not-leak",
    )

    class Query:
        def __init__(self, model):
            self.model = model

        def filter(self, *_args):
            return self

        def all(self):
            if self.model is plaza.Service:
                return [manual]
            if self.model is plaza.PlazaServicePreference:
                return []
            if self.model is plaza.PlazaServiceProfile:
                return []
            return [server]

    class Db:
        def query(self, model):
            return Query(model)

    @contextmanager
    def fake_db():
        yield Db()

    monkeypatch.setattr(plaza, "get_db", fake_db)
    monkeypatch.setattr(
        plaza,
        "_health_checks",
        lambda catalog: {
            item["key"]: {"status": "up", "http_status": 200, "latency_ms": 1, "health_error": ""}
            for item in catalog
        },
    )
    rows = plaza.list_plaza_services()
    row = next(item for item in rows if item["key"] == "manual-manual-1")
    assert row["manual"] is True
    assert row["health_url"].endswith("/health")
    assert row["auth_mode"] == "local"
    assert "account" not in row and "password" not in row


def test_scanned_web_service_is_merged_into_plaza(monkeypatch):
    server = SimpleNamespace(id="server-pve", host="10.66.66.3", name="主机2 PVE")
    scanned = SimpleNamespace(
        id="scan-1", server_id=server.id, source="agent", hidden=False,
        name="PVE Guest App", url="http://10.66.66.31:18080/", health_path=None,
        description="PVE 来宾应用", category="应用服务", icon="server",
        account="", password="",
    )

    class Query:
        def __init__(self, model):
            self.model = model

        def filter(self, *_args):
            return self

        def all(self):
            if self.model is plaza.Service:
                return [scanned]
            if self.model in (plaza.PlazaServicePreference, plaza.PlazaServiceProfile):
                return []
            return [server]

    class Db:
        def query(self, model):
            return Query(model)

    @contextmanager
    def fake_db():
        yield Db()

    monkeypatch.setattr(plaza, "get_db", fake_db)
    monkeypatch.setattr(
        plaza,
        "_health_checks",
        lambda catalog: {
            item["key"]: {"status": "up", "http_status": 200, "latency_ms": 1, "health_error": ""}
            for item in catalog
        },
    )
    row = next(item for item in plaza.list_plaza_services() if item["key"] == "scan-scan-1")
    assert row["service_id"] == "scan-1"
    assert row["scanned"] is True and row["manual"] is False
    assert row["source"] == "agent"


def test_catalog_visibility_is_persisted(monkeypatch):
    created = []

    class Query:
        def filter(self, *_args):
            return self

        def first(self):
            return None

    class Db:
        def query(self, _model):
            return Query()

        def add(self, row):
            created.append(row)

        def commit(self):
            pass

    @contextmanager
    def fake_db():
        yield Db()

    monkeypatch.setattr(plaza, "get_db", fake_db)
    result = plaza.update_catalog_visibility(
        "gitea", plaza.PlazaVisibilityUpdate(hidden=True),
    )
    assert result == {"ok": True, "key": "gitea", "hidden": True}
    assert created[0].catalog_key == "gitea"
    assert created[0].hidden is True


def test_hidden_catalog_entries_are_listed_without_credentials(monkeypatch):
    preference = SimpleNamespace(catalog_key="gitea", hidden=True)

    class Query:
        def __init__(self, model):
            self.model = model

        def filter(self, *_args):
            return self

        def all(self):
            if self.model is plaza.PlazaServicePreference:
                return [preference]
            return []

    class Db:
        def query(self, model):
            return Query(model)

    @contextmanager
    def fake_db():
        yield Db()

    monkeypatch.setattr(plaza, "get_db", fake_db)
    rows = plaza.list_hidden_plaza_services()
    assert len(rows) == 1
    assert rows[0]["key"] == "gitea"
    assert rows[0]["kind"] == "catalog"
    assert rows[0]["deletable"] is False
    assert "password" not in rows[0] and "account" not in rows[0]
