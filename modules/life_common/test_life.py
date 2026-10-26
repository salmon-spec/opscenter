import calendar
from contextlib import nullcontext
import importlib
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from runtime.auth import SessionVerifier
from life_common.core import check_revision
from subscriptions.main import Subscription, preview_csv, safe_cell
from home_inventory.main import attachment_type
from life_reminders.main import Task, calendar_interval
from life_reminders.notifications import event_jobs, task_jobs, webhook_target


def test_subscription_money_and_dates():
    body = Subscription(name='家庭会员', price='19.99', currency_id=1, next_payment='2028-02-29')
    assert body.form()['price'] == '19.99' and body.form()['notify'] == 0
    assert 'payment_method_id' not in body.form(create=True)
    assert body.form()['payment_method_id'] == ''
    for bad in [dict(price='NaN'), dict(price='0.001'), dict(next_payment='2027-02-29'), dict(url='javascript:alert(1)')]:
        with pytest.raises(ValidationError):
            Subscription.model_validate(body.model_dump() | bad)


def test_csv_preview_rejects_errors_and_marks_duplicates(monkeypatch):
    monkeypatch.setattr('subscriptions.main.subscriptions', lambda: [{'name': '现有'}])
    result = preview_csv('name,price,currency_id,next_payment\n现有,9,1,2027-01-01\n新建,2,1,2027-01-01\n新建,3,1,2027-01-02\n错,NaN,1,wrong\n')
    assert [x['duplicate'] for x in result['rows']] == [True, False, True]
    assert len(result['errors']) == 1
    assert safe_cell('=HYPERLINK("bad")').startswith("'")


def test_attachments_require_supported_magic():
    assert attachment_type(b'%PDF-1.4\n')[0] == 'application/pdf'
    assert attachment_type(b'\x89PNG\r\n\x1a\n')[0] == 'image/png'
    with pytest.raises(HTTPException):
        attachment_type(b'<script>alert(1)</script>')


def test_task_requires_timezone_and_due_date_for_repeat():
    with pytest.raises(ValidationError):
        Task(title='工作', project_id=1, due_date='2026-10-26T10:00:00')
    with pytest.raises(ValidationError):
        Task(title='工作', project_id=1, repeat='daily')


def test_annual_repeat_keeps_leap_day_anchor():
    policy = {'month': 2, 'day': 29}
    first = calendar_interval('2027-02-28T01:00:00Z', policy)
    assert first == 366 * 86400
    second = calendar_interval('2028-02-29T01:00:00Z', policy)
    assert second == 365 * 86400
    monthly = {'kind':'monthly','month':1,'day':31}
    assert calendar_interval('2027-02-28T01:00:00Z', monthly) == 31 * 86400
    # Shanghai Oct 1 01:00 is Sep 30 UTC: advance the local calendar month.
    assert calendar_interval('2027-09-30T17:00:00Z', {'kind':'monthly','month':10,'day':1}) == 31 * 86400


def test_event_identity_changes_when_source_date_changes():
    item = {'id': 'x', 'date': '2027-01-01', 'title': '保修', 'offsets': [7, 0]}
    first = event_jobs('home-inventory', [item])
    assert first[1]['due_at'].isoformat() == '2027-01-01T01:00:00+00:00'
    assert first[0]['id'] != first[1]['id']
    assert first[0]['id'] == event_jobs('home-inventory', [item])[0]['id']
    assert first[0]['id'] != event_jobs('home-inventory', [item | {'date': '2027-01-02'}])[0]['id']
    assert task_jobs([{'done': True, 'id': 1}]) == []
    a = task_jobs([{'id':1,'title':'x','due_date':'2027-01-01T01:00:00.123Z'}])
    b = task_jobs([{'id':1,'title':'x','due_date':'2027-01-01T09:00:00+08:00'}])
    assert a[0]['id'] == b[0]['id']


def test_webhook_blocks_private_networks_and_credentials(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', ('127.0.0.1', 443))])
    for url in ['https://example.com/hook', 'http://example.com/hook', 'https://user:password@example.com', 'file:///tmp/x']:
        with pytest.raises(HTTPException):
            webhook_target(url)


@pytest.mark.parametrize('module', ['subscriptions', 'home_inventory', 'life_reminders'])
def test_api_authentication_and_viewer_write_rejection(monkeypatch, module):
    app = importlib.import_module(module + '.main').app
    client = TestClient(app)
    assert client.get('/api/v1/module').status_code == 401
    monkeypatch.setattr(SessionVerifier, 'verify', lambda self, token: {'username': 'test', 'role': 'viewer'})
    endpoint = {'subscriptions': '/subscriptions', 'home_inventory': '/items', 'life_reminders': '/tasks'}[module]
    response = client.post('/api/v1' + endpoint, json={}, headers={'Authorization': 'Bearer test', 'Idempotency-Key': str(uuid4())})
    assert response.status_code == 403
    assert client.get('/health/live').status_code == 200


def test_homebox_rejects_stale_updates():
    with pytest.raises(HTTPException) as exc:
        check_revision({'updatedAt': 'new'}, 'old', 'updatedAt')
    assert exc.value.status_code == 409


def test_homebox_listing_hydrates_warranty_fields(monkeypatch):
    from home_inventory import main
    main.cached_detail.cache_clear()
    monkeypatch.setattr(main.upstream, 'get', lambda path: {'warrantyExpires': '2027-10-26', 'modelNumber': 'model'})
    row = main.hydrated({'id': 'item', 'name': '家电', 'updatedAt': 'revision', 'entityType': {'isLocation': False}})
    assert row['warrantyExpires'] == '2027-10-26' and row['modelNumber'] == 'model'
    main.cached_detail.cache_clear()


def test_task_completion_preserves_upstream_fields(monkeypatch):
    from life_reminders import main
    row = {'id': 8, 'title': '提醒', 'done': False, 'due_date': '2027-01-01T01:00:00Z', 'priority': 3, 'labels': [{'id': 4}]}
    monkeypatch.setattr(main.upstream, 'get', lambda path: row.copy())
    monkeypatch.setattr(main.store, 'preference', lambda *a: {})
    monkeypatch.setattr(main.store, 'once', lambda key, action, data, actor, operation: operation())
    monkeypatch.setattr(main.store, 'target_lock', lambda key: nullcontext())
    monkeypatch.setattr(main.upstream, 'request', lambda method, path, body: body)
    result = main.complete(8, main.Completion(done=True), uuid4(), {'username': 'test'})
    assert result['done'] and result['priority'] == 3 and result['labels'] == [{'id': 4}]
