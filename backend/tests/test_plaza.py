"""Curated service plaza contract tests (no network or database required)."""

from collections import Counter
from contextlib import contextmanager
from types import SimpleNamespace

from app import plaza


class _Query:
    def all(self):
        return [
            SimpleNamespace(id="server-vm1", host="10.66.66.4", name="虚拟-ubuntu"),
            SimpleNamespace(id="server-vm2", host="192.168.1.153", name="VM-2"),
            SimpleNamespace(id="server-vm3", host="10.66.66.6", name="虚拟机3 resolver"),
            SimpleNamespace(id="server-pve", host="10.66.66.3", name="主机2 PVE"),
        ]


class _Db:
    def query(self, _model):
        return _Query()


@contextmanager
def _fake_db():
    yield _Db()


def test_catalog_contains_exactly_the_approved_web_services():
    catalog = plaza.load_catalog()
    assert len(catalog) == 14
    assert {item["key"] for item in catalog} == {
        "gitea", "gitlab", "jenkins", "nexus", "sonarqube",
        "opsbox", "ai-hub", "apollo-portal", "token-monitor", "sanshengliubu",
        "grafana", "prometheus", "keycloak", "pve",
    }
    assert all(item["enabled"] for item in catalog)
    assert Counter(item["category"] for item in catalog) == {
        "代码与CI/CD": 5,
        "应用服务": 5,
        "监控与日志": 2,
        "安全与运维": 2,
    }
    by_key = {item["key"]: item for item in catalog}
    assert by_key["sonarqube"]["auth_mode"] == "keycloak"
    assert "/sessions/init/oidc" in by_key["sonarqube"]["entry_url"]
    assert by_key["nexus"]["auth_mode"] == "local"


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
    assert len(rows) == 14
    assert all(row["status"] == "up" for row in rows)
    assert all("password" not in row and "account" not in row and "client_secret" not in row for row in rows)
    assert {row["auth_mode"] for row in rows} == {"none", "local", "keycloak"}
