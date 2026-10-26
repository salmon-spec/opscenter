from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from fastapi import HTTPException
import requests
from life_common.core import secret, timestamp

LOCAL = timezone(timedelta(hours=8))


def cipher():
    return Fernet(secret('LIFE_CREDENTIAL_KEY').encode())


def webhook_target(url, resolve=True):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        port = parsed.port or 443
        allowed = os.getenv('LIFE_WEBHOOK_ALLOWED_HOSTS', '').split(',')
        if not host or parsed.username or parsed.password or parsed.fragment or parsed.scheme not in {'https', 'http'}:
            raise ValueError()
        if parsed.scheme != 'https' and host not in allowed:
            raise ValueError()
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM) if resolve else []
        if resolve and (not addresses or (host not in allowed and any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses))):
            raise ValueError()
        return parsed, addresses
    except (ValueError, OSError):
        raise HTTPException(422, '通知地址必须是可信HTTPS地址，内网地址须由部署白名单允许') from None


def settings(store):
    value = store.preference('notification', {})
    return {'enabled': bool(value.get('enabled')), 'configured': bool(value.get('ciphertext')),
            'mode': 'webhook' if value.get('enabled') and value.get('ciphertext') else 'in_app'}


def save_settings(store, body):
    value = store.preference('notification', {})
    if body['url']:
        webhook_target(body['url'])
        value['ciphertext'] = cipher().encrypt(body['url'].encode()).decode()
    if body['enabled'] and not value.get('ciphertext'):
        raise HTTPException(422, '请先填写通知地址')
    value['enabled'] = body['enabled']
    store.set_preference('notification', value)
    return settings(store)


def send_webhook(url, delivery):
    parsed, addresses = webhook_target(url)
    family, kind, proto, _, sockaddr = addresses[0]
    sock = socket.socket(family, kind, proto)
    sock.settimeout(8)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), timeout=8)
    try:
        sock.connect(sockaddr)
        conn.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname) if parsed.scheme == 'https' else sock
        body = json.dumps({'id': delivery['id'], 'title': delivery['title'], 'source': delivery['source'],
                           'due_at': delivery['due_at'].isoformat()}, ensure_ascii=False).encode()
        conn.request('POST', parsed.path + ('?' + parsed.query if parsed.query else '') or '/', body,
                     {'Content-Type': 'application/json', 'X-OpsCenter-Delivery-ID': delivery['id']})
        result = conn.getresponse()
        if not 200 <= result.status < 300:
            raise ValueError('delivery rejected')
    finally:
        conn.close()
        sock.close()


def source_events(source):
    prefix = 'LIFE_' + source.upper().replace('-', '_')
    base = os.getenv(prefix + '_URL', '').rstrip('/')
    if not base:
        raise ValueError('source not configured')
    with requests.Session() as session:
        session.trust_env = False
        with session.get(base + '/api/v1/internal/due',
                         headers={'X-OpsCenter-Module-Token': secret(prefix + '_TOKEN')},
                         timeout=(3, 15), allow_redirects=False, stream=True) as response:
            response.raise_for_status()
            content = bytearray()
            for part in response.iter_content(65536):
                content.extend(part)
                if len(content) > 2 * 1024 * 1024:
                    raise ValueError('source too large')
            return json.loads(content)['items']


def event_jobs(source, events):
    jobs = []
    for event in events:
        due = datetime.fromisoformat(event['date'] + 'T09:00:00+08:00')
        for offset in event.get('offsets', [0]):
            jobs.append(make_job(source, event['id'], event['date'] + ':' + str(offset), event['title'], due - timedelta(days=offset)))
    return jobs


def make_job(source, source_id, occurrence, title, due):
    key = hashlib.sha256(json.dumps([source, str(source_id), occurrence]).encode()).hexdigest()
    return {'id': key, 'source': source, 'source_id': str(source_id), 'occurrence': occurrence,
            'title': title[:300], 'due_at': due.astimezone(timezone.utc)}


def task_jobs(rows):
    jobs = []
    for task in rows:
        if task.get('done') or not task.get('reminder_enabled', True):
            continue
        reminders = [x.get('reminder') for x in (task.get('reminders') or [])]
        for value in reminders or [task.get('due_date')]:
            if not value or value.startswith('0001-'):
                continue
            due = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if due.utcoffset() is None:
                continue
            due = due.astimezone(timezone.utc).replace(microsecond=0)
            jobs.append(make_job('tasks', task['id'], due.isoformat(), task['title'], due))
    return jobs


def scan(store, task_provider, now=None):
    now = now or datetime.now(timezone.utc)
    errors, sources = {}, {}
    # A transaction-scoped lock makes scheduled and manual scans share one delivery owner.
    with store.connect() as db:
        if not db.execute('SELECT pg_try_advisory_xact_lock(20261026) AS locked').fetchone()['locked']:
            return {'busy': True}
        activated = store.preference('activated_at')
        if not activated:
            activated = now.isoformat()
            store.set_preference('activated_at', activated)
        earliest = datetime.fromisoformat(activated)
        try:
            sources['tasks'] = task_jobs(task_provider())
        except Exception:
            errors['tasks'] = '待办来源读取失败'
        for source in ['subscriptions', 'home-inventory']:
            try:
                sources[source] = event_jobs(source, source_events(source))
            except Exception:
                errors[source] = '到期来源读取失败'
        for source, jobs in sources.items():
            active = [job['id'] for job in jobs]
            db.execute("UPDATE life_deliveries SET state='cancelled',updated_at=now() WHERE source=%s AND state IN ('pending','in_app','failed') AND NOT (id=ANY(%s))", (source, active))
            for job in jobs:
                if job['due_at'] < earliest:
                    continue
                db.execute('''INSERT INTO life_deliveries (id,source,source_id,occurrence,title,due_at)
                  VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title''',
                           tuple(job[k] for k in ['id', 'source', 'source_id', 'occurrence', 'title', 'due_at']))
        config = store.preference('notification', {})
        external = config.get('enabled') and config.get('ciphertext')
        quiet = now.astimezone(LOCAL).hour >= 22 or now.astimezone(LOCAL).hour < 8
        rows = db.execute('''SELECT * FROM life_deliveries WHERE state='pending' AND NOT acknowledged
          AND due_at<=%s AND (retry_at IS NULL OR retry_at<=%s)
          AND (snoozed_until IS NULL OR snoozed_until<=%s) ORDER BY due_at LIMIT 50 FOR UPDATE''', (now, now, now)).fetchall()
        for row in rows:
            if row['source'] not in sources or (external and quiet):
                continue
            state, error = 'in_app', ''
            attempts = row['attempts']
            if external:
                attempts += 1
                try:
                    send_webhook(cipher().decrypt(config['ciphertext'].encode()).decode(), row)
                    state = 'sent'
                except Exception:
                    state = 'failed' if attempts >= 3 else 'pending'
                    error = '通知投递失败，远端可能已接收，请核对投递编号'
            db.execute('''UPDATE life_deliveries SET state=%s,attempts=%s,last_error=%s,retry_at=%s,updated_at=%s WHERE id=%s''',
                       (state, attempts, error, now + timedelta(minutes=2 ** attempts) if error else None, now, row['id']))
    result = {'checked_at': now.isoformat(), 'errors': errors, 'sources': list(sources), 'notification_mode': settings(store)['mode']}
    store.set_preference('scheduler', result)
    return result


def deliveries(store):
    with store.connect() as db:
        rows = db.execute('SELECT * FROM life_deliveries ORDER BY due_at DESC LIMIT 500').fetchall()
    return {'items': rows, 'scheduler': store.preference('scheduler', {}), 'notification': settings(store)}


def valid_id(value):
    if not re.fullmatch('[0-9a-f]{64}', value):
        raise HTTPException(422, '提醒编号无效')


def acknowledge(store, delivery_id):
    valid_id(delivery_id)
    with store.connect() as db:
        row = db.execute('UPDATE life_deliveries SET acknowledged=true,updated_at=now() WHERE id=%s RETURNING id', (delivery_id,)).fetchone()
    if not row:
        raise HTTPException(404, '提醒不存在')
    return {'acknowledged': True}


def snooze(store, delivery_id, minutes):
    valid_id(delivery_id)
    with store.connect() as db:
        row = db.execute("""UPDATE life_deliveries SET snoozed_until=now()+(%s * interval '1 minute'),
          state='pending',attempts=0,retry_at=NULL,acknowledged=false,updated_at=now()
          WHERE id=%s AND state<>'cancelled' RETURNING id""", (minutes, delivery_id)).fetchone()
    if not row:
        raise HTTPException(404, '提醒不存在或已取消')
    return {'snoozed_minutes': minutes}
