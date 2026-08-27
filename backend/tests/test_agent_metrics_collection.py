"""Regression tests for Agent metric collection transaction boundaries."""
from contextlib import contextmanager
from types import SimpleNamespace

from app import main


def test_agent_network_fetch_runs_without_open_db_session(monkeypatch):
    server = SimpleNamespace(
        id="server-1", host="192.168.1.152", agent_type="remote",
        agent_port=19100, agent_token="token", agent_status="running",
    )
    state = {"db_open": False, "context_number": 0, "commits": 0}

    class Query:
        def __init__(self, result=None):
            self.result = result

        def filter(self, *_args):
            return self

        def all(self):
            return self.result or []

        def first(self):
            return self.result

        def delete(self, **_kwargs):
            return 0

    class Db:
        def __init__(self, context_number):
            self.context_number = context_number

        def query(self, model):
            if model is main.Server:
                return Query([server] if self.context_number == 1 else server)
            return Query()

        def commit(self):
            state["commits"] += 1

    @contextmanager
    def fake_db():
        state["context_number"] += 1
        state["db_open"] = True
        yield Db(state["context_number"])
        state["db_open"] = False

    def fake_fetch(*_args):
        assert not state["db_open"], "Agent request ran while a DB session was open"
        return None

    monkeypatch.setattr(main, "get_db", fake_db)
    monkeypatch.setattr(main, "fetch_agent_metrics", fake_fetch)

    main._collect_agent_metrics()

    assert server.agent_status == "stopped"
    assert state["commits"] == 2
