"""Regression tests for the unified service health coordinator."""
from contextlib import contextmanager
from types import SimpleNamespace

from app import service_health


def _service(url="http://192.168.1.10:8080", status="up"):
    return SimpleNamespace(
        id="svc-1", name="demo", url=url, health_path="",
        server_id="server-1", status=status,
    )


def test_http_auth_responses_are_reachable(monkeypatch):
    for status_code in (200, 302, 401, 403):
        monkeypatch.setattr(
            service_health.requests,
            "get",
            lambda *_args, status_code=status_code, **_kwargs: SimpleNamespace(status_code=status_code),
        )
        assert service_health._check_service(_service()) == (True, "")


def test_unsupported_or_placeholder_url_is_skipped():
    assert service_health._check_service(_service("#systemd:sshd"))[0] is None
    assert service_health._check_service(_service("postgresql://192.168.1.10:5432"))[0] is None


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

    @contextmanager
    def fake_db():
        yield Db()

    monkeypatch.setattr(service_health, "get_db", fake_db)
    assert service_health.run_service_health_cycle() == 1
    assert updates == []
    assert service_health.get_health_snapshot()[0]["status"] == "degraded"

    assert service_health.run_service_health_cycle() == 1
    assert updates == ["down"]
    assert service_health.get_health_snapshot()[0]["status"] == "down"


def test_network_probe_runs_after_snapshot_db_session_closes(monkeypatch):
    svc = _service()
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
