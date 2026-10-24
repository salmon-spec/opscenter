import copy
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import center
import engine
import main
import notify
import sources
from test_main import store


@pytest.fixture(autouse=True)
def clean_auth():
    yield
    main.app.dependency_overrides.clear()


def state():
    return {'events': [], 'rules': [], 'silences': [], 'policies': [], 'deliveries': [], 'notes': [], 'meta': {}}


def rule(**extra):
    return {'id': 'r', 'name': 'CPU', 'metric': 'cpu', 'operator': '>', 'threshold': '80',
            'value_type': 'numeric', 'duration_sec': 0, 'recovery_sec': 0, 'enabled': True, **extra}


def sample(now, value=90, **extra):
    return {'id': 'h1', 'name': 'node', 'source': 'host', 'value': value, 'observed_at': now.isoformat(), **extra}


def test_metric_health_uses_current_inventory_and_detects_missing_samples():
    now = datetime.now(timezone.utc)
    inventory = {'status': 'ok', 'collected_at': now.isoformat(), 'items': [{'id': 'h1'}]}
    metrics = {'collected_at': now.isoformat(), 'items': [
        {'id': 'h1', 'last_sample_at': now.isoformat(), 'metrics': {'cpu': 10}},
        {'id': 'retired', 'last_sample_at': (now - timedelta(days=3)).isoformat()},
    ]}
    def get(url, token, path):
        return inventory if path.endswith('/hosts') else metrics
    assert sources.collect(get, lambda key, default='': default, [], now)[1]['metrics']['status'] == 'ok'
    metrics['items'] = metrics['items'][1:]
    assert sources.collect(get, lambda key, default='': default, [], now)[1]['metrics']['status'] == 'degraded'


def test_silence_keeps_rule_label_after_rule_archival(store):
    data = state()
    data['rules'] = [rule(archived=True)]
    data['silences'] = [{'id': 's', 'rule_id': 'r', 'ended_at': datetime.now(timezone.utc).isoformat()}]
    store.value = (data, datetime.now(timezone.utc))
    assert TestClient(main.app).get('/api/v1/silences').json()[0]['rule_name'] == 'CPU'


def fired(now):
    data = state()
    data['rules'] = [rule()]
    engine.transition(data, rule(), sample(now), now)
    return data


def test_duration_recovery_hysteresis_and_ack_are_independent():
    now = datetime.now(timezone.utc)
    data = state()
    config = rule(duration_sec=120, recovery_sec=60, recovery_threshold='70')
    engine.transition(data, config, sample(now), now)
    assert data['events'][0]['status'] == 'pending'
    engine.transition(data, config, sample(now + timedelta(seconds=121)), now + timedelta(seconds=121))
    event = data['events'][0]
    assert event['status'] == 'firing'
    event['handling_status'] = 'acked'
    engine.transition(data, config, sample(now + timedelta(seconds=180), 75), now + timedelta(seconds=180))
    assert event['status'] == 'firing'
    engine.transition(data, config, sample(now + timedelta(seconds=200), 60), now + timedelta(seconds=200))
    assert event['status'] == 'firing'
    engine.transition(data, config, sample(now + timedelta(seconds=261), 60), now + timedelta(seconds=261))
    assert event['status'] == 'recovered' and event['handling_status'] == 'acked'


def test_missing_data_resets_recovery_and_pending_continuity():
    now = datetime.now(timezone.utc)
    data = fired(now)
    config = rule(recovery_sec=60)
    engine.transition(data, config, sample(now + timedelta(seconds=1), 0), now + timedelta(seconds=1))
    engine.transition(data, config, sample(now + timedelta(seconds=70), None), now + timedelta(seconds=70))
    event = data['events'][0]
    assert event['status'] == 'firing' and event['data_status'] == 'unknown'
    engine.transition(data, config, sample(now + timedelta(seconds=71), 0), now + timedelta(seconds=71))
    assert event['status'] == 'firing'


def test_certificates_on_same_host_have_distinct_incidents():
    now = datetime.now(timezone.utc)
    data = state()
    for cert in ['a', 'b']:
        engine.transition(data, rule(), sample(now, id='cert:' + cert, source='certificate', server_id='h1'), now)
    assert len(data['events']) == 2
    assert len({e['fingerprint'] for e in data['events']}) == 2


def test_silence_does_not_stop_evaluation_or_recovery():
    now = datetime.now(timezone.utc)
    data = fired(now)
    data['silences'] = [{'starts_at': (now - timedelta(hours=1)).isoformat(), 'ends_at': (now + timedelta(hours=1)).isoformat(), 'resource_id': 'h1', 'reason': '维护'}]
    engine.plan_notifications(data, now)
    assert data['events'][0]['suppressed_reason'] == '维护'
    engine.transition(data, rule(), sample(now, 0), now)
    assert data['events'][0]['status'] == 'recovered'
    engine.transition(data, rule(), sample(now + timedelta(seconds=1)), now + timedelta(seconds=1))
    assert len(data['events']) == 2  # a genuine recurrence is recorded even in the old cooldown


def policy(**extra):
    return {'id': 'p', 'name': '通知', 'enabled': True, 'channel': 'inapp', 'dry_run': False,
            'max_notifications': 3, 'repeat_sec': 3600, 'notify_recovery': True, **extra}


def test_outbox_dedup_and_recovery_only_after_firing_was_accepted():
    now = datetime.now(timezone.utc)
    data = fired(now)
    data['policies'] = [policy()]
    engine.plan_notifications(data, now)
    engine.plan_notifications(data, now)
    assert len(data['deliveries']) == 1
    engine.transition(data, rule(), sample(now, 0), now)
    engine.plan_notifications(data, now)
    assert len(data['deliveries']) == 1
    data['deliveries'][0]['status'] = 'accepted'
    engine.plan_notifications(data, now)
    assert len(data['deliveries']) == 2
    assert data['deliveries'][1]['kind'] == 'recovered'


def test_notifications_stop_on_ack_unknown_and_migrated_events():
    now = datetime.now(timezone.utc)
    for extra in [{'handling_status': 'acked'}, {'data_status': 'unknown'}, {'notification_eligible': False}]:
        data = fired(now)
        data['events'][0].update(extra)
        data['policies'] = [policy()]
        engine.plan_notifications(data, now)
        assert not data['deliveries']


def test_quiet_hours_and_repeat_limit():
    now = datetime(2026, 10, 21, 15, tzinfo=timezone.utc)  # 23:00 CST
    data = fired(now)
    data['policies'] = [policy(quiet_start=22*60, quiet_end=8*60)]
    engine.plan_notifications(data, now)
    assert not data['deliveries']
    data['policies'][0].update(quiet_start=None, quiet_end=None, max_notifications=1)
    engine.plan_notifications(data, now)
    data['deliveries'][0]['status'] = 'accepted'
    engine.plan_notifications(data, now + timedelta(hours=2))
    assert len(data['deliveries']) == 1


def test_worker_dry_run_and_real_inapp_delivery(store):
    now = datetime.now(timezone.utc)
    data = fired(now)
    data['policies'] = [policy(dry_run=True)]
    store.save(data, now)
    center.dispatch_once(lambda: store)
    assert data['deliveries'][0]['status'] == 'previewed'
    assert 'preview' in data['deliveries'][0]
    data['policies'][0].update(id='real', dry_run=False)
    assert center.dispatch_once(lambda: store)
    assert data['deliveries'][-1]['status'] == 'accepted'


def test_worker_timeout_is_not_blindly_retried(store, monkeypatch):
    now = datetime.now(timezone.utc)
    data = fired(now)
    data['policies'] = [policy()]
    store.save(data, now)
    monkeypatch.setattr(notify, 'send', lambda *args: (_ for _ in ()).throw(TimeoutError()))
    assert center.dispatch_once(lambda: store)
    assert data['deliveries'][0]['status'] == 'uncertain'
    assert not center.dispatch_once(lambda: store)
    assert len(data['deliveries']) == 1


def test_api_ack_close_comments_and_archive_preserve_history(store, monkeypatch):
    now = datetime.now(timezone.utc)
    data = fired(now)
    store.save(data, now)
    main.app.dependency_overrides[main.app.state.require_editor] = lambda: {'username': 'alice', 'role': 'editor'}
    client = TestClient(main.app)
    event = data['events'][0]
    assert client.post(f"/api/v1/events/{event['id']}/close").status_code == 409
    assert client.post(f"/api/v1/events/{event['id']}/ack").status_code == 200
    assert event['status'] == 'firing' and event['handling_status'] == 'acked'
    assert client.post(f"/api/v1/events/{event['id']}/comments", json={'text': '已排查'}).status_code == 200
    assert client.get(f"/api/v1/events/{event['id']}").json()['notes'][0]['text'] == '已排查'
    engine.transition(data, rule(), sample(now, 0), now)
    assert client.post(f"/api/v1/events/{event['id']}/ack").status_code == 409
    assert client.post(f"/api/v1/events/{event['id']}/close").status_code == 200
    assert client.delete('/api/v1/rules/r').status_code == 200
    assert len(data['events']) == 1
    assert client.get('/api/v1/rules').json() == []


def test_search_includes_old_active_events_and_today_uses_fired_time(store):
    now = datetime.now(timezone.utc)
    data = fired(now)
    data['events'][0]['created_at'] = (now - timedelta(days=90)).isoformat()
    store.save(data, now)
    client = TestClient(main.app)
    assert client.get('/api/v1/events/search?status=active&days=7').json()['total'] == 1
    assert client.get('/api/v1/events/search?today=true').json()['total'] == 1
    assert client.get('/api/v1/events/search?severity=critical').json()['total'] == 0
    assert client.get('/api/v1/events/search?page_size=101').status_code == 422


def test_policy_destinations_are_encrypted_and_not_returned(store, monkeypatch, tmp_path):
    key = tmp_path / 'key'
    key.write_bytes(Fernet.generate_key())
    monkeypatch.setenv('ALERTS_CREDENTIAL_KEY_FILE', str(key))
    store.save(state(), datetime.now(timezone.utc))
    user = lambda: {'username': 'admin', 'role': 'admin'}
    main.app.dependency_overrides[main.app.state.require_admin] = user
    main.app.dependency_overrides[main.app.state.current_user] = user
    client = TestClient(main.app)
    assert client.post('/api/v1/policies', json={'name':'微信','channel':'pushplus','token':'private-token'}).status_code == 201
    stored = store.value[0]['policies'][0]
    assert 'private-token' not in json.dumps(stored)
    public = client.get('/api/v1/policies').json()[0]
    assert public['configured'] and 'destination_cipher' not in public and 'token' not in public
    assert client.put('/api/v1/policies/' + public['id'], json={'name':'微信2','channel':'pushplus'}).status_code == 200
    assert notify.unseal(store.value[0]['policies'][0]['destination_cipher'])['token'] == 'private-token'


@pytest.mark.parametrize('threshold', ['NaN', 'inf', '-inf', 'nonsense'])
def test_invalid_numeric_threshold_is_rejected(threshold):
    with pytest.raises(ValueError):
        main.RulePayload(name='CPU', metric='cpu', threshold=threshold)


def test_all_new_write_routes_require_editor_or_admin(store):
    store.save(state(), datetime.now(timezone.utc))
    client = TestClient(main.app)
    for url, payload in [('/api/v1/policies', {'name':'x'}), ('/api/v1/events/ack-batch', {'ids':['x']}),
                          ('/api/v1/events/x/comments', {'text':'x'}), ('/api/v1/events/x/close', {}), ('/api/v1/deliveries/x/retry', {})]:
        assert client.post(url, json=payload).status_code == 401


def test_private_webhook_is_blocked_without_explicit_allowlist(monkeypatch):
    monkeypatch.delenv('ALERTS_WEBHOOK_ALLOWED_HOSTS', raising=False)
    with pytest.raises(ValueError):
        notify.destination('https://127.0.0.1/send')
    with pytest.raises(ValueError):
        notify.destination('http://example.com/send', resolve=False)


def test_webhook_local_receiver_accepts_json_and_idempotency_header(monkeypatch):
    seen = []
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append((self.path, self.headers['X-OpsCenter-Delivery-ID'], json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{}')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('ALERTS_WEBHOOK_ALLOWED_HOSTS', '127.0.0.1')
    try:
        assert notify.post_json(f'http://127.0.0.1:{server.server_port}/hook', {'value': '测试'}, request_id='job-1')[0] == 200
        assert seen == [('/hook', 'job-1', {'value':'测试'})]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_stale_service_sample_is_unknown_not_a_recovery():
    now = datetime.now(timezone.utc)
    env = lambda key, default='': 'configured' if key.startswith('SERVICE_CATALOG') else default
    def get(url, token, path):
        if path.endswith('alert-targets'):
            return {'status':'ok','collected_at':now.isoformat(),'items':[{'id':'svc','name':'svc','metric':'service_status','value':'up','observed_at':(now-timedelta(hours=1)).isoformat()}]}
        return None
    observations, health = sources.collect(get, env, [], now)
    assert observations['service_status'][0]['value'] is None


def test_overnight_recurring_silence_uses_previous_weekday():
    # Wednesday 01:00 CST belongs to the Tuesday 23:00–02:00 maintenance window.
    now = datetime(2026,10,20,17,tzinfo=timezone.utc)
    silence = {'starts_at':(now-timedelta(days=1)).isoformat(),'ends_at':(now+timedelta(days=1)).isoformat(),
               'weekdays':[1],'daily_start':23*60,'daily_end':2*60,'reason':'维护'}
    assert engine.silenced({}, [silence], now) == '维护'


def test_notification_grouping_and_escalation_respect_limits(store, monkeypatch):
    now = datetime.now(timezone.utc)
    data = fired(now)
    engine.transition(data, rule(), sample(now, id='h2'), now)
    data['policies'] = [policy(group_by='source', escalate_after_sec=600)]
    engine.plan_notifications(data, now)
    assert not data['deliveries']
    data['policies'][0]['escalate_after_sec'] = 0
    store.save(data, now)
    calls = []
    monkeypatch.setattr(notify, 'send', lambda p,e,j: (calls.append(j) or ('accepted','')))
    assert center.dispatch_once(lambda:store)
    assert len(calls) == 1 and len(calls[0]['group_content']['event_ids']) == 2
    assert all(j['status'] == 'accepted' for j in data['deliveries'])


def test_electricity_owner_prevents_duplicate_external_notifications():
    now = datetime.now(timezone.utc)
    data = state()
    config = rule(metric='electricity_alert')
    data['rules'] = [config]
    engine.transition(data, config, sample(now, source='electricity', notification_owner='electricity'), now)
    data['policies'] = [policy(id='wx',channel='pushplus'),policy(id='station')]
    engine.plan_notifications(data, now)
    assert [job['policy_id'] for job in data['deliveries']] == ['station']


def test_nonfinite_observation_never_recovers_an_active_fault():
    now = datetime.now(timezone.utc)
    data = fired(now)
    engine.transition(data, rule(), sample(now, value=float('nan')), now)
    assert data['events'][0]['status'] == 'firing'
    assert data['events'][0]['data_status'] == 'unknown'


def test_expired_worker_lease_is_held_for_manual_verification(store):
    now = datetime.now(timezone.utc)
    data = fired(now)
    data['policies'] = [policy()]
    engine.plan_notifications(data, now)
    data['deliveries'][0].update(status='sending',lease_until=(now-timedelta(seconds=1)).isoformat())
    store.save(data, now)
    assert not center.dispatch_once(lambda:store)
    assert data['deliveries'][0]['status'] == 'uncertain'


def test_legacy_certificate_identity_is_adopted_only_when_unambiguous():
    from storage import legacy_event
    now = datetime.now(timezone.utc)
    config = rule(metric='cert_days_left', operator='<', threshold='30')
    old = {'id':'old','rule_id':'r','status':'firing','server_id':'host','server_name':'example.com','created_at':now.isoformat(),'fired_at':now.isoformat()}
    certificates = [{'id':'a','server_id':'host','domain':'example.com'}]
    migrated = legacy_event(old, [config], certificates)
    data = state(); data['events'] = [migrated]
    engine.transition(data, config, sample(now, value=10, id='cert:a', source='certificate', server_id='host'), now)
    assert len(data['events']) == 1 and data['events'][0]['id'] == 'old'
    assert migrated['notification_eligible'] is False
    ambiguous = legacy_event(old,[config],certificates + [{'id':'b','server_id':'host','domain':'example.com'}])
    assert not ambiguous.get('fingerprint') and ambiguous['data_status'] == 'unknown'
