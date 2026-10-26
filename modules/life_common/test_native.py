import time
from contextlib import contextmanager
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from life_common import native


class TicketStore:
    def __init__(self):
        self.rows = {}

    def set_preference(self, key, value):
        self.rows[key] = value

    @contextmanager
    def connect(self):
        yield self

    def execute(self, query, params):
        value = self.rows.pop(params[0], None) if 'RETURNING' in query else None
        return SimpleNamespace(fetchone=lambda: {'value': value} if value else None)


def client(monkeypatch, key='subscriptions', name='wallos'):
    monkeypatch.setenv('LIFE_NATIVE_PARENTS', 'http://workbench.test https://workbench.test')
    monkeypatch.setenv('LIFE_NATIVE_HTTP_ORIGIN', 'http://native.test:18091')
    monkeypatch.setenv('LIFE_NATIVE_HTTPS_ORIGIN', 'https://native.test:18491')
    monkeypatch.setattr(native, 'secret', lambda name: 's' * 48)
    monkeypatch.setattr(native, 'native_login', lambda *a: ('native-user-token', []))
    app = FastAPI()
    app.state.store = TicketStore()
    app.state.require_admin = lambda: {'username': 'owner', 'role': 'admin'}
    native.register_native(app, key, SimpleNamespace(name=name, base='http://upstream.test'))
    return TestClient(app)


def issue(c, parent='http://workbench.test'):
    token = jwt.encode({'exp': int(time.time()) + 300}, 'workbench-secret-for-test', algorithm='HS256')
    return c.post('/api/v1/native/session', json={'parent_origin': parent},
                  headers={'Authorization': 'Bearer ' + token})


def test_one_use_ticket_and_separate_cookie_audience(monkeypatch):
    c = client(monkeypatch)
    ticket = issue(c).json()['ticket']
    assert c.post('/_ops/launch', data={'ticket': ticket}, headers={'Origin': 'https://evil.test'}).status_code == 403
    r = c.post('/_ops/launch', data={'ticket': ticket}, headers={'Origin': 'http://workbench.test'})
    assert r.status_code == 200 and 'HttpOnly' in r.headers['set-cookie']
    assert 'frame-ancestors http://workbench.test https://workbench.test' in r.headers['content-security-policy']
    assert c.post('/_ops/launch', data={'ticket': ticket}, headers={'Origin': 'http://workbench.test'}).status_code == 401
    assert c.get('/_ops/verify').status_code == 204
    assert c.get('/_ops/verify', headers={'X-Forwarded-Method': 'POST', 'Origin': 'https://evil.test'}).status_code == 403
    assert c.get('/_ops/verify', headers={'X-Forwarded-Method': 'POST', 'Origin': 'http://native.test:18091'}).status_code == 204
    token = c.cookies.get('ops_native_subscriptions_http')
    with pytest.raises(jwt.InvalidAudienceError):
        jwt.decode(token, 's' * 48, algorithms=['HS256'], audience='home-inventory')
    expired = jwt.encode({'sub': 'owner', 'aud': 'subscriptions', 'iss': 'opscenter-native', 'exp': 1}, 's' * 48, algorithm='HS256')
    c.cookies.set('ops_native_subscriptions_http', expired, domain='testserver.local', path='/')
    assert c.get('/_ops/verify').status_code == 401


def test_tls_launch_and_token_stays_in_upstream_origin(monkeypatch):
    c = client(monkeypatch, key='life-reminders', name='vikunja')
    ticket = issue(c, 'https://workbench.test').json()['ticket']
    r = c.post('/_ops/launch', data={'ticket': ticket}, headers={'Origin': 'https://workbench.test'})
    assert 'Secure' in r.headers['set-cookie'] and 'ops_native_life_reminders_tls' in r.headers['set-cookie']
    assert 'native-user-token' in r.text and 'localStorage.setItem("API_URL",location.origin)' in r.text
    assert c.get('/_ops/verify').status_code == 401
    assert issue(c, 'https://evil.test').status_code == 403


def test_proxy_cookie_allowlist():
    raw = 'ops-access-token=workbench; ops_native_subscriptions_http=gate; http_hb.auth.token=private; http_PHPSESSID=php; http_vikunja_refresh_token=refresh; tls_PHPSESSID=tls'
    assert native.upstream_cookies(raw, 'wallos') == 'PHPSESSID=php'
    assert native.upstream_cookies(raw, 'homebox') == 'hb.auth.token=private'
    assert native.upstream_cookies(raw, 'vikunja') == 'vikunja_refresh_token=refresh'
    assert native.upstream_cookies(raw, 'wallos', True) == 'PHPSESSID=tls'
    assert native.upstream_cookies('PHPSESSID=unscoped', 'wallos') == ''
