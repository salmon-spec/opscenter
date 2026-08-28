"""Curated service plaza contract tests (no network or database required)."""

from collections import Counter
from contextlib import contextmanager
from types import SimpleNamespace

from app import plaza


class _Query:
    def __init__(self, model):
        self.model = model

    def filter(self, *_args):
        return self

    def all(self):
        if self.model is plaza.Service:
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
            return [manual] if self.model is plaza.Service else [server]

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
