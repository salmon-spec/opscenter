"""Regression cases from the 2026-10-20 full source audit. Isolated DB only."""
from contextlib import contextmanager
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import text
from fastapi.testclient import TestClient
from app import main, ssh_manager
from app.database import engine, SessionLocal
from app.models import Base, Server
from app.metrics_history import _thin
from app.services.tasks import _sanitize


@pytest.fixture(autouse=True)
def database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def test_host_credentials_encrypted_at_rest_and_legacy_rows_readable():
    identifier = uuid.uuid4()
    secret = '__password__synthetic-ssh-password'
    with SessionLocal() as db:
        db.add(Server(id=identifier, name='host', host='127.0.0.2', ssh_key=secret))
        db.commit()
        stored = db.execute(text('SELECT ssh_key FROM servers')).scalar_one()
        assert stored.startswith('encrypted:ssh:v1:') and secret not in stored
        db.expire_all()
        assert db.get(Server, identifier).ssh_key == secret
        db.execute(text('UPDATE servers SET ssh_key=:legacy'), {'legacy': secret})
        db.commit()
        db.expire_all()
        assert db.get(Server, identifier).ssh_key == secret


def test_host_registration_preserves_inventory_uuid_and_execution_address(monkeypatch):
    monkeypatch.setattr(main, 'publish_host_inventory_telemetry', lambda _: True)
    identifier = str(uuid.uuid4())
    client = TestClient(main.app)
    assert client.post('/api/v2/servers', json={'id': identifier, 'name': 'node', 'host': '10.0.0.2'}).status_code == 201
    assert client.put('/api/v2/servers/'+identifier, json={'lan_ip': '192.168.1.2', 'ssh_port': 2222}).status_code == 200
    from app.system_control import _server
    row = _server(identifier)
    assert row.lan_ip == '192.168.1.2' and row.ssh_port == 2222


def test_failed_database_health_returns_503(monkeypatch):
    @contextmanager
    def offline():
        def fail(_):
            raise RuntimeError('offline')
        yield SimpleNamespace(execute=fail)
    monkeypatch.setattr(main, 'get_db', offline)
    response = TestClient(main.app).get('/api/v2/health')
    assert response.status_code == 503 and response.json()['database'] is False


@pytest.mark.parametrize('count', [2501, 3000, 4999, 5000, 10001])
def test_thinning_preserves_full_time_interval(count):
    points = _thin(list(range(count)))
    assert len(points) == 2500 and points[0] == 0 and points[-1] == count-1
    assert len(set(points)) == len(points)


@pytest.mark.parametrize('message', ['Bearer synthetic-secret', 'agent_token=synthetic-secret', '__password__synthetic-secret',
                                     'ssh_key=synthetic-secret', 'Authorization: Bearer synthetic-secret',
                                     'password="synthetic-secret with spaces"', 'token=synthetic-secret'])
def test_task_redaction_removes_secret_values(message):
    assert 'synthetic-secret' not in _sanitize(message)


def test_legacy_credentials_can_be_migrated_without_changing_ssh_value():
    from backend.scripts.encrypt_host_credentials import migrate
    identifier = uuid.uuid4()
    with SessionLocal() as db:
        db.add(Server(id=identifier, name='legacy', host='127.0.0.3'))
        db.commit()
        db.execute(text('UPDATE servers SET ssh_key=:value'), {'value':'__password__synthetic-legacy'})
        db.commit()
    assert migrate() == 1
    assert migrate() == 0
    with SessionLocal() as db:
        assert db.get(Server, identifier).ssh_key == '__password__synthetic-legacy'
        assert 'synthetic-legacy' not in db.execute(text('SELECT ssh_key FROM servers')).scalar_one()


def test_ssh_drains_both_streams_before_waiting_for_exit():
    class Channel:
        def __init__(self):
            self.out = [b'a' * 65536] * 40
            self.err = [b'warning']
            self.closed = False
        def recv_ready(self): return bool(self.out)
        def recv_stderr_ready(self): return bool(self.err)
        def recv(self, size): return self.out.pop()
        def recv_stderr(self, size): return self.err.pop()
        def exit_status_ready(self): return not self.out and not self.err
        def recv_exit_status(self):
            assert not self.out and not self.err
            return 0
        def close(self): self.closed = True
    channel = Channel()
    client = SimpleNamespace(exec_command=lambda *a, **k: (None, SimpleNamespace(channel=channel), None))
    out, err, code = ssh_manager.ssh_exec(client, 'synthetic', timeout=5)
    assert code == 0 and len(out) == 65536*40 and err == 'warning' and channel.closed
