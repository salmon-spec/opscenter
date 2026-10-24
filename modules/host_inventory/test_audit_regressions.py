import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from fastapi import HTTPException
import pytest
import main


def test_registration_reuses_uuid_for_core_create_and_retry_update(monkeypatch):
    calls = []
    state = {}
    class Response(io.BytesIO):
        def __exit__(self, *args): self.close()
    class Opener:
        def open(self, request, timeout):
            method = request.get_method()
            calls.append((method, request.full_url, json.loads(request.data) if request.data else None))
            assert request.get_header('Authorization') == 'Bearer session'
            if method == 'GET' and not state:
                raise HTTPError(request.full_url, 404, 'missing', {}, None)
            if method == 'POST':
                state.update(json.loads(request.data))
            if method == 'PUT':
                state.update(json.loads(request.data))
            return Response(json.dumps(state).encode())
    monkeypatch.setattr(main, 'build_opener', lambda *args: Opener())
    request = SimpleNamespace(headers={'authorization': 'Bearer session'})
    payload = main.HostCreate(name='node', host='10.0.0.2', lan_ip='192.168.1.2', ssh_password='synthetic')
    main.register_management_host('stable-id', payload, request)
    main.register_management_host('stable-id', payload, request)
    assert [call[0] for call in calls] == ['GET', 'POST', 'GET', 'PUT']
    assert calls[1][2]['id'] == 'stable-id' and calls[-1][1].endswith('/stable-id')
    assert state['lan_ip'] == '192.168.1.2'


def test_registration_failure_stops_asset_write(monkeypatch):
    from fastapi.testclient import TestClient
    calls = []
    def offline(*args): raise HTTPException(502, 'temporarily unavailable')
    monkeypatch.setattr(main, 'register_management_host', offline)
    monkeypatch.setattr(main, 'HostInventoryStore', lambda: SimpleNamespace(create_host=lambda *a: calls.append(a)))
    main.app.dependency_overrides[main.app.state.require_editor] = lambda: {'username': 'admin'}
    try:
        response = TestClient(main.app).post('/api/v1/hosts', json={'name': 'node', 'host': '10.0.0.2'})
        assert response.status_code == 502 and not calls
    finally:
        main.app.dependency_overrides.clear()
