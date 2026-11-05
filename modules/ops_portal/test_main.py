import importlib.util
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient


@pytest.fixture()
def module(tmp_path, monkeypatch):
    private = Ed25519PrivateKey.generate()
    public = tmp_path / 'public.pem'
    public.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    monkeypatch.setenv('OPS_JWT_PUBLIC_KEY_FILE', str(public))
    monkeypatch.setenv('OPS_ADMIN_USER', 'admin')
    monkeypatch.setenv('MODULE_AUTH_REQUIRED', 'true')
    monkeypatch.setenv('PORTAL_DB_PATH', str(tmp_path / 'portal.sqlite3'))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    spec = importlib.util.spec_from_file_location('portal_test_instance', Path(__file__).with_name('main.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    def token(role='admin', expired=False):
        now = datetime.now(timezone.utc)
        return {'Authorization': 'Bearer ' + jwt.encode({'sub': '1', 'username': 'admin', 'role': role,
            'iss': 'opscenter', 'aud': 'opscenter-modules', 'exp': now + timedelta(minutes=-1 if expired else 5)}, private, algorithm='EdDSA')}
    with TestClient(mod.app) as client:
        yield mod, client, token


def test_auth_roles_and_health(module):
    _, c, token = module
    assert c.get('/health/ready').status_code == 200
    assert c.get('/').status_code == 200
    assert 'portal-login' in c.get('/').text
    assert c.get('/api/v1/catalogue').status_code == 401
    assert c.get('/api/v1/catalogue', headers=token(expired=True)).status_code == 401
    data = c.get('/api/v1/catalogue', headers=token('viewer')).json()
    assert len(data['apps']) == 26
    assert c.post('/api/v1/catalogue', headers=token('viewer'), json=data).status_code == 403
    assert c.get('/api/v1/audit', headers=token('viewer')).status_code == 403


def test_persistence_conflict_and_audit(module):
    mod, c, token = module
    data = c.get('/api/v1/catalogue', headers=token()).json()
    data['apps'][0]['description'] = '独立保存验证'
    result = c.post('/api/v1/catalogue', headers=token(), json=data)
    assert result.status_code == 200
    assert result.json()['revision'] == 2
    assert c.post('/api/v1/catalogue', headers=token(), json=data).status_code == 409
    mod.initialize()
    assert mod.read_catalogue()['apps'][0]['description'] == '独立保存验证'
    audit = c.get('/api/v1/audit', headers=token()).json()['items']
    assert len(audit) == 1 and audit[0]['actor'] == 'admin' and audit[0]['revision'] == 2


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'file:///tmp/x', 'http://user:pass@example.com', 'http://x:wrong', 'https://x/\nsecret', 'http://x\\y'])
def test_url_validation(module, url):
    _, c, token = module
    data = c.get('/api/v1/catalogue', headers=token()).json()
    data['apps'][0]['url'] = url
    assert c.post('/api/v1/catalogue', headers=token(), json=data).status_code == 422
    assert c.get('/api/v1/catalogue', headers=token()).json()['revision'] == 1


def test_duplicate_and_parent_references(module):
    _, c, token = module
    data = c.get('/api/v1/catalogue', headers=token()).json()
    data['apps'].append(data['apps'][0])
    assert c.post('/api/v1/catalogue', headers=token(), json=data).status_code == 422
    data['apps'].pop()
    data['apps'][-1]['parent_app'] = 'missing-parent'
    assert c.post('/api/v1/catalogue', headers=token(), json=data).status_code == 422


def test_management_bundling_and_login_failure(module, monkeypatch):
    mod, c, token = module
    ui = c.get('/ui/').text
    assert 'ops-module-ready' in ui and 'MANAGEMENT_JS' not in ui and 'MANAGEMENT_CSS' not in ui
    assert c.get('/static/../seed.json').status_code == 404
    def unavailable(*args, **kwargs):
        raise mod.requests.ConnectionError()
    monkeypatch.setattr(mod.requests, 'post', unavailable)
    assert c.post('/session/login', json={'username': 'admin', 'password': 'test-only'}).status_code == 503
