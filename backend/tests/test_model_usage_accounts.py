"""Provider contracts and account configuration, without real API keys or calls."""
import json
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import auth, extension_modules as routes
from app.credential_crypto import decrypt_secret
from app.database import Base, engine, get_db
from app.models import CommandCodeUsageSnapshot, ExtensionModule
from app.services.command_code import CommandCodeUsageError, CommandCodeUsageMonitor, command_code
from app.services.model_usage import ModelUsageAccounts, ProviderUsageMonitor, model_usage

app = FastAPI()
app.include_router(routes.router)
client = TestClient(app)
URL = '/api/v2/modules/command-code-usage'


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(auth, 'AUTH_ENABLED', False)
    monkeypatch.setattr(auth, 'OPERATOR_TOKEN', '')
    command_code.configure(None)
    model_usage.configure(None)
    model_usage._monitors.clear()
    routes.seed_extension_modules()
    with get_db() as db:
        row = db.query(ExtensionModule).filter_by(key='command-code-usage').one()
        row.running = True
        db.commit()
    yield
    Base.metadata.drop_all(engine)


def go_payload():
    return {'usage': {key: {'percent': percent, 'status': 'ok', 'resetInSec': 120}
                      for key, percent in [('rolling', 25), ('weekly', 40), ('monthly', 50)]}}


def fake_json(self, endpoint, query=None, config=None):
    if endpoint == '/usage':
        return go_payload()
    if endpoint == '/user/balance':
        return {'is_available': False, 'balance_infos': [
            {'currency': 'CNY', 'total_balance': '0.00', 'granted_balance': '0.00', 'topped_up_balance': '0.00'},
            {'currency': 'USD', 'total_balance': '1.25', 'granted_balance': '0.25', 'topped_up_balance': '1.00'}]}
    raise AssertionError(endpoint)


def profiles():
    return {'accounts': [
        {'id': 'go', 'name': 'Go 工作', 'provider': 'opencode-go', 'api_key': 'test-go-secret'},
        {'id': 'ds', 'name': 'DeepSeek', 'provider': 'deepseek', 'api_key': 'test-ds-secret'},
    ], 'active_account_id': 'go', 'display_account_ids': ['go', 'ds']}


def test_save_select_preserve_keys_and_encrypt(monkeypatch):
    monkeypatch.setattr(ProviderUsageMonitor, '_request_json', fake_json)
    saved = client.post(URL+'/config', json=profiles())
    assert saved.status_code == 200, saved.text
    data = saved.json()
    assert len(data['accounts']) == 2 and 'test-go-secret' not in saved.text
    with get_db() as db:
        ciphertext = db.query(ExtensionModule).filter_by(key='command-code-usage').one().config_ciphertext
        assert 'test-ds-secret' not in ciphertext
        assert json.loads(decrypt_secret(ciphertext))['accounts'][0]['api_key'] == 'test-go-secret'
    payload = profiles()
    for a in payload['accounts']:
        a['api_key'] = ''
    payload['active_account_id'] = 'ds'
    assert client.post(URL+'/config', json=payload).status_code == 200
    go = client.get(URL+'/status?account_id=go&force=true').json()['health']
    ds = client.get(URL+'/status?force=true').json()['health']
    assert go['provider'] == 'opencode-go' and go['windows']['five_hour']['percent'] == 25
    assert 'usage' not in go and 'credits' not in go
    assert ds['provider'] == 'deepseek' and ds['balances'][1]['total_balance'] == 1.25
    assert ds['available'] and not ds['can_use_api'] and 'usage' not in ds
    assert client.get(URL+'/status?account_id=missing').status_code == 404


def test_blank_key_cannot_be_forwarded_to_changed_endpoint(monkeypatch):
    monkeypatch.setattr(ProviderUsageMonitor, '_request_json', fake_json)
    assert client.post(URL+'/config', json=profiles()).status_code == 200
    payload = profiles()
    payload['accounts'][0].update(api_key='', api_base='https://other.example')
    response = client.post(URL+'/config', json=payload)
    assert response.status_code == 400
    assert client.get(URL+'/config').json()['accounts'][0]['api_base'] == 'https://opencode.ai/zen/go/v1'


@pytest.mark.parametrize('mutate', ['duplicate', 'active', 'display', 'invalid_key', 'nan'])
def test_invalid_config_does_not_replace_saved_config(monkeypatch, mutate):
    monkeypatch.setattr(ProviderUsageMonitor, '_request_json', fake_json)
    assert client.post(URL+'/config', json=profiles()).status_code == 200
    payload = profiles()
    if mutate == 'duplicate': payload['accounts'][1]['id'] = 'go'
    if mutate == 'active': payload['active_account_id'] = 'missing'
    if mutate == 'display': payload['display_account_ids'] = ['missing']
    if mutate == 'invalid_key': payload['accounts'][0]['api_key'] = 'bad'
    if mutate == 'nan': payload['accounts'][0]['timeout_seconds'] = 'NaN'
    assert client.post(URL+'/config', json=payload).status_code == 400
    assert client.get(URL+'/config').json()['active_account_id'] == 'go'


@pytest.mark.parametrize('provider,payload', [('opencode-go', {'usage': {'rolling': {'percent': 1}}}), ('deepseek', {'is_available': True, 'balance_infos': [{'currency': 'CNY', 'total_balance': 'NaN'}]})])
def test_contract_changes_are_errors_not_zero(monkeypatch, provider, payload):
    monitor = ProviderUsageMonitor(provider)
    monitor.configure({'api_key': 'test-secret', 'api_base': 'https://example.com'})
    monkeypatch.setattr(monitor, '_request_json', lambda *a, **kw: payload)
    result = monitor.status(force=True)
    assert not result['available'] and result['error']


def test_legacy_go_schema_and_reset_time(monkeypatch):
    monitor = ProviderUsageMonitor('opencode-go')
    monitor.configure({'api_key': 'test-secret', 'api_base': 'https://example.com'})
    monkeypatch.setattr(monitor, '_request_json', lambda *a, **kw: {
        key: {'usagePercent': 0, 'resetInSec': 50} for key in ['rollingUsage', 'weeklyUsage', 'monthlyUsage']})
    result = monitor.status(force=True)
    assert result['available'] and result['windows']['five_hour']['percent'] == 0
    assert result['windows']['monthly']['resets_at'].endswith('Z')


def test_restart_snapshot_is_scoped_to_selected_account():
    with get_db() as db:
        db.add_all([CommandCodeUsageSnapshot(scope_key='account:first', total_count=3, captured_at=datetime(2026, 1, 1)),
                    CommandCodeUsageSnapshot(scope_key='account:second', total_count=999, captured_at=datetime(2026, 2, 1))])
        db.commit()
    monitor = CommandCodeUsageMonitor()
    monitor.history_scope = 'account:first'
    assert monitor.snapshot_status()['usage']['total_count'] == 3
    monitor.history_scope = 'account:missing'
    assert monitor.snapshot_status() is None


def test_legacy_config_preserves_history_scope_and_secret(monkeypatch):
    manager = ModelUsageAccounts()
    old = {'api_key': 'legacy-secret', 'api_base': 'https://api.commandcode.ai'}
    manager.configure(old)
    with get_db() as db:
        db.add(CommandCodeUsageSnapshot(scope_key='a'*32, total_count=12))
        db.commit()
    data = manager.config_summary()
    account = {k: data['accounts'][0][k] for k in ['id', 'name', 'provider', 'api_base', 'org_id', 'refresh_interval_seconds', 'timeout_seconds']}
    config = manager.prepare_config({'accounts': [account]}, old)
    assert config['accounts'][0]['history_scope'] == 'a'*32
    assert config['accounts'][0]['api_key'] == 'legacy-secret'
    manager.configure(config)
    assert manager.status()['usage']['total_count'] == 12


def test_authentication_still_required(monkeypatch):
    monkeypatch.setattr(auth, 'AUTH_ENABLED', True)
    assert client.get(URL+'/config').status_code == 401
    assert client.post(URL+'/config', json=profiles()).status_code == 401


def test_missing_credit_limits_and_rolling_totals_are_not_fake_quota():
    monitor = CommandCodeUsageMonitor()
    result = monitor._normalize_usage({}, {}, {}, {'totalCredits': 62.8, 'periodBasis': 'last-30-days'}, [], 1, {'api_base': 'https://example.com'})
    assert result['credits']['remaining_total'] is None
    assert result['credits']['usage_percent'] is None
    assert result['windows']['five_hour']['percent'] is None
    baseline = CommandCodeUsageSnapshot(captured_at=datetime(2026, 1, 1), total_credits=0, period_start=None)
    window = monitor._history_window(baseline, result, datetime(2026, 1, 2), 86400)
    assert window['unavailable'] and window['credits'] is None and not window['complete']
