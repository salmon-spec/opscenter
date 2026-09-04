"""Regression tests for the unified service health coordinator."""
from contextlib import contextmanager
from types import SimpleNamespace

from app import service_health


def _service(url="http://192.168.1.10:8080", status="up", health_path="/"):
    return SimpleNamespace(
        id="svc-1", name="demo", url=url, health_path=health_path,
        server_id="server-1", status=status,
    )


def test_http_auth_responses_are_reachable(monkeypatch):
    for status_code in (200, 302, 401, 403):
        monkeypatch.setattr(
            service_health.requests,
            "get",
            lambda *_args, status_code=status_code, **_kwargs: SimpleNamespace(status_code=status_code),
        )
        result = service_health._check_service(_service())
        assert result[:2] == (True, "")
        assert result[2] == status_code
        assert result[3] >= 0


def test_private_https_skips_certificate_verification(monkeypatch):
    calls = []
    monkeypatch.setattr(
        service_health.requests,
        "get",
        lambda *_args, **kwargs: calls.append(kwargs) or SimpleNamespace(status_code=200),
    )

    service_health._check_service(_service("https://10.66.66.3:8006/"))
    service_health._check_service(_service("https://example.com/"))

    assert calls[0]["verify"] is False
    assert calls[1]["verify"] is True


def test_unsupported_or_placeholder_url_is_skipped():
    assert service_health._check_service(_service("#systemd:sshd"))[0] is None
    assert service_health._check_service(_service("postgresql://192.168.1.10:5432"))[0] is None


def test_auto_discovered_url_without_health_path_is_not_authoritative(monkeypatch):
    monkeypatch.setattr(service_health, "SERVICE_HEALTH_PROBE_UNCONFIGURED", False)
    assert service_health._check_service(_service(health_path=""))[0] is None


def test_snapshot_is_unknown_before_first_probe(monkeypatch):
    service_health._health_state.clear()
    monkeypatch.setattr(service_health, "_snapshot_targets", lambda: ([_service()], {}))
    assert service_health.get_health_snapshot()[0]["status"] == "unknown"


def test_failure_threshold_controls_persisted_down_state(monkeypatch):
    service_health._health_state.clear()
    svc = _service(status="up")
    monkeypatch.setattr(service_health, "SERVICE_HEALTH_FAIL_THRESHOLD", 2)
    monkeypatch.setattr(service_health, "_snapshot_targets", lambda: ([svc], {}))
    monkeypatch.setattr(service_health, "_check_service", lambda _svc: (False, "timeout"))
    monkeypatch.setattr(service_health, "_fire_alert", lambda *_args: None)

    updates = []
    history = []

    class Query:
        def filter(self, *_args):
            return self

        def update(self, values):
            updates.append(next(iter(values.values())))

    class Db:
        def query(self, _model):
            return Query()

        def commit(self):
            pass

        def add(self, row):
            history.append(row)

    @contextmanager
    def fake_db():
        yield Db()

    monkeypatch.setattr(service_health, "get_db", fake_db)
    assert service_health.run_service_health_cycle() == 1
    assert updates == []
    assert len(history) == 1
    assert service_health.get_health_snapshot()[0]["status"] == "degraded"

    assert service_health.run_service_health_cycle() == 1
    assert updates == ["down"]
    assert len(history) == 2
    assert service_health.get_health_snapshot()[0]["status"] == "down"


def test_network_probe_runs_after_snapshot_db_session_closes(monkeypatch):
    svc = _service(status="unknown")
    state = {"db_open": False}

    class Query:
        def __init__(self, result):
            self.result = result

        def filter(self, *_args):
            return self

        def all(self):
            return self.result

    class Db:
        def query(self, model):
            return Query([svc] if model is service_health.Service else [])

    @contextmanager
    def fake_db():
        state["db_open"] = True
        yield Db()
        state["db_open"] = False

    monkeypatch.setattr(service_health, "get_db", fake_db)
    monkeypatch.setattr(
        service_health,
        "_check_service",
        lambda _svc: (_ for _ in ()).throw(AssertionError("DB session leaked into network probe"))
        if state["db_open"] else (None, "skip"),
    )
    assert service_health.run_service_health_cycle() == 0


def test_snapshot_excludes_plaza_owned_manual_service(monkeypatch):
    manual = _service()
    other = SimpleNamespace(**{**manual.__dict__, "id": "svc-2", "name": "other"})

    class Query:
        def __init__(self, rows): self.rows = rows
        def filter(self, *_args): return self
        def all(self): return self.rows

    class Db:
        def query(self, model):
            return Query([manual, other] if model is service_health.Service else [])

    @contextmanager
    def fake_db(): yield Db()

    monkeypatch.setattr(service_health, "get_db", fake_db)
    from app import plaza
    monkeypatch.setattr(plaza, "plaza_owned_service_ids", lambda _db: {manual.id})
    services, _servers = service_health._snapshot_targets()
    assert [service.id for service in services] == [other.id]
