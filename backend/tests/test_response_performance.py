"""Bound database work and expensive concurrent dashboard refreshes."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading
import time

import pytest
from fastapi import Request, Response
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app import dashboard, main
from app.models import Base, Server, Service


@pytest.fixture
def inventory(monkeypatch):
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for i in range(9):
            host = Server(name=f'node-{i}', host=f'192.0.2.{i+1}')
            db.add(host)
            db.flush()
            if i < 8:
                db.add(Service(server_id=host.id, name=f'app-{i}', url='http://example.invalid'))
        db.commit()
    queries = []
    event.listen(engine, 'before_cursor_execute', lambda *args: queries.append(args[2]))
    @contextmanager
    def get_db():
        with Session(engine) as db:
            yield db
    monkeypatch.setattr(main, 'get_db', get_db)
    monkeypatch.setattr(main, 'engine', engine)
    yield queries
    engine.dispose()


def test_startup_skips_existing_columns(inventory, capsys):
    main._ensure_new_columns()
    assert not any(sql.lstrip().upper().startswith('ALTER TABLE') for sql in inventory)
    assert '[migrate]' not in capsys.readouterr().out


def test_host_list_uses_two_queries_and_preserves_empty_host(inventory):
    rows = main.list_servers(Response())
    assert len(rows) == 9
    assert sorted(row['service_count'] for row in rows) == [0] + [1]*8
    assert len(inventory) <= 2


@pytest.mark.parametrize('handler', [main.list_services_with_status, main.list_all_services])
def test_service_lists_batch_hosts(inventory, handler):
    rows = handler(server_id=None)
    assert len(rows) == 8
    assert all(row['server_name'].startswith('node-') for row in rows)
    assert len(inventory) <= 2


def test_simultaneous_screen_refresh_builds_once(monkeypatch):
    dashboard.reset_screen_cache()
    calls = []
    gate = threading.Barrier(8)
    def build():
        assert not dashboard._SCREEN_CACHE_LOCK.locked()
        calls.append(1)
        time.sleep(.05)
        return {'servers': [], 'generation': len(calls)}, '2026-11-05T00:00:00Z'
    monkeypatch.setattr(dashboard, '_build_screen_business', build)
    def request(_):
        gate.wait(timeout=5)
        return dashboard.get_screen_summary(Request({'type':'http', 'headers':[]}))
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(request, range(8)))
        assert all(response.status_code == 200 for response in responses)
        assert len({response.headers['etag'] for response in responses}) == 1
        assert len(calls) == 1
    finally:
        dashboard.reset_screen_cache()


def test_failed_screen_build_can_be_retried(monkeypatch):
    dashboard.reset_screen_cache()
    def fail():
        raise RuntimeError('fixture outage')
    request = Request({'type':'http', 'headers':[]})
    monkeypatch.setattr(dashboard, '_build_screen_business', fail)
    with pytest.raises(RuntimeError):
        dashboard.get_screen_summary(request)
    monkeypatch.setattr(dashboard, '_build_screen_business', lambda: ({'servers':[]}, 'now'))
    try:
        assert dashboard.get_screen_summary(request).status_code == 200
    finally:
        dashboard.reset_screen_cache()
