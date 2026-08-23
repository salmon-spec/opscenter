"""Agent health reconciliation regression tests."""

from contextlib import contextmanager
from types import SimpleNamespace

from app import main


class _Query:
    def __init__(self, result):
        self.result = result

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.result

    def all(self):
        return self.result


class _Db:
    def __init__(self, remotes):
        self.remotes = remotes
        self.query_count = 0
        self.committed = False

    def query(self, _model):
        self.query_count += 1
        return _Query(None if self.query_count == 1 else self.remotes)

    def commit(self):
        self.committed = True


def _server(status="stopped"):
    return SimpleNamespace(
        name="remote",
        host="10.0.0.8",
        agent_type="remote",
        agent_status=status,
        agent_port=19100,
        agent_token="stale",
        agent_version="2.1.0",
        last_seen=None,
    )


def test_stopped_agent_recovers_and_refreshes_its_token(monkeypatch):
    server = _server("stopped")
    db = _Db([server])

    @contextmanager
    def fake_db():
        yield db

    monkeypatch.setattr(main, "get_db", fake_db)
    monkeypatch.setattr(
        main,
        "check_agent_status",
        lambda _srv: {
            "status": "running",
            "agent_port": 19100,
            "agent_token": "fresh",
            "agent_version": "2.2.0",
        },
    )
    monkeypatch.setattr(
        main,
        "fetch_agent_metrics",
        lambda _host, _port, token: {"cpu_percent": 1} if token == "fresh" else None,
    )

    main._run_agent_health_check()

    assert server.agent_status == "running"
    assert server.agent_token == "fresh"
    assert server.agent_version == "2.2.0"
    assert server.last_seen is not None
    assert db.committed


def test_missing_agent_remains_explicitly_not_deployed(monkeypatch):
    server = _server("stopped")
    db = _Db([server])

    @contextmanager
    def fake_db():
        yield db

    monkeypatch.setattr(main, "get_db", fake_db)
    monkeypatch.setattr(
        main,
        "check_agent_status",
        lambda _srv: {"status": "not_deployed", "message": "Agent未部署"},
    )

    main._run_agent_health_check()

    assert server.agent_status == "not_deployed"
    assert db.committed
