from __future__ import annotations

import copy
import os
import re
import socket
import threading
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

import engine
import notify
import sources
from templates import TEMPLATES


def evaluate(store, snapshot, get):
    snapshot(required=True)
    now = datetime.now(timezone.utc)
    observations, health = sources.collect(get, os.getenv, store().read_certificates(), now)
    def apply(data):
        if (previous := engine.parse_time(data.get('meta', {}).get('last_evaluated_at'))) and previous > now:
            return False
        # Unknown/missing targets must not inherit a previous healthy or firing sample.
        for event in data['events']:
            engine.normalize(event)
            if event['condition_status'] in {'pending', 'firing'}:
                event['data_status'] = 'unknown'
        for rule in data['rules']:
            if not rule.get('enabled') or rule.get('archived'):
                continue
            targets = observations.get(rule.get('metric'), [])
            for target in targets:
                if rule.get('resource_id') and target['id'] != rule['resource_id']:
                    continue
                if rule.get('server_id') and str(target.get('server_id') or target['id']) != rule['server_id']:
                    continue
                engine.transition(data, rule, target, now)
        for event in data['events']:
            if event.get('data_status') == 'unknown' and event.get('condition_status') in {'pending', 'firing'}:
                event['recovery_started_at'] = None
                if event['condition_status'] == 'pending':
                    event['first_breached_at'] = now.isoformat()
        meta = data.setdefault('meta', {})
        meta.update(last_evaluated_at=now.isoformat(), sources=health)
        meta['targets'] = list({f"{t['source']}:{t['id']}": {k: t[k] for k in ('id', 'name', 'source', 'server_id') if k in t}
                               for group in observations.values() for t in group}.values())
        engine.plan_notifications(data, now)
    store().mutate(apply, '', '', '')
    return True


def dispatch_once(store):
    """Claim under the database transaction lock; never hold it during delivery."""
    now = datetime.now(timezone.utc)
    claimed = []
    def claim(data):
        policies = {row['id']: row for row in data.get('policies', [])}
        events = {row['id']: engine.normalize(row) for row in data['events']}
        rules = {row['id']: row for row in data['rules']}
        engine.plan_notifications(data, now)
        for job in sorted(data.get('deliveries', []), key=lambda row: row['created_at']):
            if job['status'] == 'sending' and (engine.parse_time(job.get('lease_until')) or now) < now:
                job.update(status='uncertain', error='上次发送中断，请核对平台记录后重试')
            if job['status'] not in {'queued', 'retry'} or (engine.parse_time(job.get('next_attempt_at')) or now) > now:
                continue
            policy, event = policies.get(job['policy_id']), events.get(job['event_id'])
            if not policy or not policy.get('enabled') or policy.get('archived') or not event:
                job.update(status='skipped', error='策略已停用或事件不存在')
                continue
            rule = rules.get(event.get('rule_id'), {})
            if not rule.get('enabled', True) or rule.get('archived'):
                job.update(status='skipped', error='规则已停用或归档')
                continue
            if job['kind'] != 'recovered' and (event['condition_status'] != 'firing' or event['handling_status'] != 'unacked'):
                job.update(status='skipped', error='事件已确认或结束')
                continue
            if event.get('suppressed_reason') or (job['kind'] != 'recovered' and event.get('data_status') != 'ok'):
                continue
            local = now.astimezone(timezone(timedelta(hours=8)))
            minute = local.hour * 60 + local.minute
            start, end = policy.get('quiet_start'), policy.get('quiet_end')
            if start is not None and end is not None and (start <= minute < end if start < end else minute >= start or minute < end):
                continue
            # An explicit dry-run task can never become a real send by editing a policy.
            if job.get('dry_run', True) or policy.get('dry_run', True):
                job.update(status='previewed', preview=notify.message(event, job['kind']), completed_at=now.isoformat())
                continue
            if claimed:
                first_job, first_policy, first_event = claimed[0]
                group = first_policy.get('group_by', 'none')
                if policy['id'] != first_policy['id'] or job['kind'] != first_job['kind']:
                    continue
                if group == 'source' and event['source'] != first_event['source']:
                    continue
                if group == 'host' and (not event.get('server_id') or event['server_id'] != first_event.get('server_id')):
                    continue
            job.update(status='sending', claim_id=str(uuid.uuid4()), attempts=job.get('attempts', 0) + 1,
                       lease_until=(now + timedelta(seconds=90)).isoformat())
            claimed.append((copy.deepcopy(job), copy.deepcopy(policy), copy.deepcopy(event)))
            if policy.get('group_by', 'none') == 'none' or len(claimed) >= 8:
                break
    store().mutate(claim, '', '', '')
    if not claimed:
        return False
    job, policy, event = claimed[0]
    if len(claimed) > 1:
        parts = [notify.message(e, j['kind']) for j, _, e in claimed]
        text = '\n\n'.join(part['text'] for part in parts)
        job['group_content'] = {'title': f"{len(claimed)} 条{'恢复通知' if job['kind'] == 'recovered' else '故障告警'}",
                                'text': text if len(text) <= 5000 else text[:5000] + '\n完整明细请查看告警中心。',
                                'event_ids': [e['id'] for _, _, e in claimed], 'kind': job['kind']}
    try:
        status, error = notify.send(policy, event, job)
    except (socket.timeout, TimeoutError, ConnectionError, OSError):
        status, error = 'uncertain', '连接中断或响应超时，请核对平台记录'
    except Exception:
        status, error = 'failed', '发送失败，请检查渠道地址、密钥和平台配置'
    completed = datetime.now(timezone.utc)
    def finish(data):
        for claimed_job, _, _ in claimed:
            row = next((item for item in data['deliveries'] if item['id'] == claimed_job['id']), None)
            if not row or row.get('claim_id') != claimed_job['claim_id'] or row['status'] != 'sending':
                continue
            final_status = 'failed' if status == 'retry' and row['attempts'] >= 4 else status
            row.update(status=final_status, error=error, completed_at=completed.isoformat(), group_request_id=job['id'],
                       next_attempt_at=(completed + timedelta(seconds=min(900, 30 * 2 ** (row['attempts'] - 1)))).isoformat())
    store().mutate(finish, '', '', '')
    return True


class CommentPayload(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class BatchPayload(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)


class PolicyPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    channel: Literal['inapp', 'webhook', 'pushplus', 'sms'] = 'inapp'
    enabled: bool = True
    dry_run: bool = True
    url: str = Field(default='', max_length=2048)
    token: str = Field(default='', max_length=2048)
    phone: str = Field(default='', max_length=20)
    sources: list[Literal['host', 'service', 'certificate', 'k3s', 'pve', 'electricity', 'datasource']] = Field(default_factory=list, max_length=7)
    severities: list[Literal['critical', 'warning', 'info']] = Field(default_factory=list, max_length=3)
    repeat_sec: int = Field(default=3600, ge=0, le=604800)
    max_notifications: int = Field(default=3, ge=0, le=100)
    group_by: Literal['none', 'source', 'host'] = 'none'
    group_wait_sec: int = Field(default=0, ge=0, le=300)
    escalate_after_sec: int = Field(default=0, ge=0, le=86400)
    notify_recovery: bool = True
    quiet_start: int | None = Field(default=None, ge=0, le=1439)
    quiet_end: int | None = Field(default=None, ge=0, le=1439)

    @model_validator(mode='after')
    def validate_quiet(self):
        if (self.quiet_start is None) != (self.quiet_end is None) or (self.quiet_start is not None and self.quiet_start == self.quiet_end):
            raise ValueError('免打扰必须同时设置不同的开始与结束时间')
        if '\r' in self.token or '\n' in self.token:
            raise ValueError('令牌格式无效')
        return self


def public_policy(row):
    return {**{key: value for key, value in row.items() if key != 'destination_cipher'},
            'configured': bool(row.get('destination_cipher')) or row['channel'] == 'inapp'}


def register(app, store, snapshot, mutate, get):
    def data():
        return snapshot(required=True)[0]

    def event_in(payload, event_id):
        event = next((row for row in payload['events'] if row['id'] == event_id), None)
        if event is None:
            raise HTTPException(404, '告警事件不存在')
        return engine.normalize(event)

    @app.get('/api/v1/overview')
    def overview():
        return engine.overview(data(), datetime.now(timezone.utc))

    @app.get('/api/v1/templates')
    def templates():
        return TEMPLATES

    @app.get('/api/v1/targets')
    def targets():
        return data().get('meta', {}).get('targets', [])

    @app.get('/api/v1/events/search')
    def search_events(q: str = Query(default='', max_length=200), status: str = '', source: str = '', severity: str = '', today: bool = False,
                      handling: str = '', days: int = Query(default=30, ge=1, le=365),
                      page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100)):
        now = datetime.now(timezone.utc)
        rows = []
        for value in data()['events']:
            row = engine.normalize(dict(value))
            # Old active incidents remain visible even outside the requested history window.
            active = row['condition_status'] in {'pending', 'firing'}
            created = engine.parse_time(row.get('created_at'))
            if today:
                fired = engine.parse_time(row.get('fired_at'))
                tz = timezone(timedelta(hours=8))
                if not fired or fired.astimezone(tz).date() != now.astimezone(tz).date():
                    continue
            if not active and (not created or (now - created).total_seconds() > days * 86400):
                continue
            if status and (row['condition_status'] not in {'pending', 'firing'} if status == 'active' else row['condition_status'] != status):
                continue
            if source and row['source'] != source or severity and row['severity'] != severity or handling and row['handling_status'] != handling:
                continue
            if q.casefold() not in ' '.join(str(row.get(k, '')) for k in ('rule_name', 'resource_name', 'server_name', 'id')).casefold():
                continue
            rows.append(row)
        rows.sort(key=lambda row: ({'critical': 0, 'warning': 1, 'info': 2}.get(row['severity'], 3), -(engine.parse_time(row.get('created_at')) or now).timestamp()))
        return {'items': rows[(page - 1) * page_size:page * page_size], 'total': len(rows), 'page': page, 'page_size': page_size}

    @app.post('/api/v1/events/ack-batch')
    def batch_ack(payload: BatchPayload, user=Depends(app.state.require_editor)):
        def apply(current):
            for event_id in set(payload.ids):
                event = event_in(current, event_id)
                if event['condition_status'] != 'firing':
                    raise HTTPException(409, '只能确认正在触发的告警')
                if event['handling_status'] != 'unacked':
                    continue
                event.update(handling_status='acked', acked_by=user['username'], acked_at=datetime.now(timezone.utc).isoformat())
                engine.stamp(event, 'acked', datetime.now(timezone.utc), user['username'])
        mutate(user['username'], 'alert.events.ack', 'batch', apply)
        return {'ok': True}

    @app.get('/api/v1/events/{event_id}')
    def event_detail(event_id: str):
        current = data()
        row = event_in(current, event_id)
        return {**row, 'notes': [note for note in current.get('notes', []) if note['event_id'] == event_id],
                'deliveries': [job for job in current.get('deliveries', []) if job['event_id'] == event_id]}

    @app.post('/api/v1/events/{event_id}/comments')
    def add_comment(event_id: str, payload: CommentPayload, user=Depends(app.state.require_editor)):
        text = payload.text.strip()
        if not text:
            raise HTTPException(422, '请填写处理记录')
        def apply(current):
            event_in(current, event_id)
            current.setdefault('notes', []).append({'id': str(uuid.uuid4()), 'event_id': event_id,
                'text': text, 'actor': user['username'], 'created_at': datetime.now(timezone.utc).isoformat()})
        mutate(user['username'], 'alert.event.comment', event_id, apply)
        return {'ok': True}

    @app.post('/api/v1/events/{event_id}/close')
    def close_event(event_id: str, user=Depends(app.state.require_editor)):
        def apply(current):
            event = event_in(current, event_id)
            if event['condition_status'] not in {'recovered', 'cancelled'}:
                raise HTTPException(409, '故障恢复后才能完成处理')
            if event['handling_status'] == 'closed':
                return False
            event.update(handling_status='closed', closed_by=user['username'], closed_at=datetime.now(timezone.utc).isoformat())
            engine.stamp(event, 'closed', datetime.now(timezone.utc), user['username'])
        mutate(user['username'], 'alert.event.close', event_id, apply)
        return {'ok': True}

    @app.get('/api/v1/policies')
    def policies(user=Depends(app.state.current_user)):
        return [public_policy(row) for row in data().get('policies', []) if not row.get('archived')]

    def save_policy(payload, policy_id, user):
        def apply(current):
            previous = next((row for row in current.setdefault('policies', []) if row['id'] == policy_id and not row.get('archived')), None)
            if policy_id and previous is None:
                raise HTTPException(404, '通知策略不存在')
            row = payload.model_dump(exclude={'url', 'token', 'phone'})
            row.update(id=policy_id or str(uuid.uuid4()), updated_at=datetime.now(timezone.utc).isoformat())
            if payload.channel != 'inapp':
                try:
                    old = notify.unseal(previous['destination_cipher']) if previous and previous.get('destination_cipher') and previous['channel'] == payload.channel else {}
                    secret = {'url': payload.url or old.get('url', ''), 'token': payload.token or old.get('token', ''), 'phone': payload.phone or old.get('phone', '')}
                    if payload.channel == 'webhook':
                        parsed, _ = notify.destination(secret['url'], resolve=False)
                        row['target_hint'] = parsed.hostname
                    elif not secret['token']:
                        raise ValueError('请填写 PushPlus 令牌')
                    if payload.channel == 'sms' and not re.fullmatch(r'\+?[0-9]{6,18}', secret['phone']):
                        raise ValueError('短信接收号码无效')
                    row['destination_cipher'] = notify.seal(secret)
                except ValueError:
                    raise HTTPException(422, '通知地址或令牌无效，请检查配置') from None
                except Exception:
                    raise HTTPException(503, '通知密钥或目标校验不可用，请检查服务端配置') from None
            if previous:
                previous.clear()
                previous.update(row)
            else:
                current['policies'].append(row)
        mutate(user['username'], 'alert.policy.save', policy_id or 'new', apply)
        return {'ok': True}

    @app.post('/api/v1/policies', status_code=201)
    def create_policy(payload: PolicyPayload, user=Depends(app.state.require_admin)):
        return save_policy(payload, None, user)

    @app.put('/api/v1/policies/{policy_id}')
    def update_policy(policy_id: str, payload: PolicyPayload, user=Depends(app.state.require_admin)):
        return save_policy(payload, policy_id, user)

    @app.delete('/api/v1/policies/{policy_id}')
    def archive_policy(policy_id: str, user=Depends(app.state.require_admin)):
        def apply(current):
            row = next((row for row in current.get('policies', []) if row['id'] == policy_id), None)
            if not row:
                raise HTTPException(404, '通知策略不存在')
            row.update(archived=True, enabled=False)
        mutate(user['username'], 'alert.policy.archive', policy_id, apply)
        return {'ok': True}

    @app.get('/api/v1/deliveries')
    def deliveries(status: str = '', page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100)):
        rows = [row for row in data().get('deliveries', []) if not status or (row['status'] in {'failed', 'uncertain'} if status == 'attention' else row['status'] == status)]
        rows.sort(key=lambda row: row['created_at'], reverse=True)
        return {'items': rows[(page - 1) * page_size:page * page_size], 'total': len(rows)}

    @app.post('/api/v1/deliveries/{job_id}/retry')
    def retry(job_id: str, user=Depends(app.state.require_admin)):
        def apply(current):
            row = next((row for row in current.get('deliveries', []) if row['id'] == job_id), None)
            if not row:
                raise HTTPException(404, '投递记录不存在')
            if row['status'] not in {'failed', 'uncertain'}:
                raise HTTPException(409, '当前记录无需重试')
            if row['attempts'] >= 10:
                raise HTTPException(409, '此任务已达到重试上限，请检查渠道')
            row.update(status='queued', error='', next_attempt_at=datetime.now(timezone.utc).isoformat())
        mutate(user['username'], 'alert.delivery.retry', job_id, apply)
        return {'ok': True}

    @app.post('/api/v1/rules/preview')
    def preview(payload: dict, user=Depends(app.state.require_editor)):
        from main import RulePayload
        try:
            rule = RulePayload.model_validate(payload).model_dump()
        except ValueError:
            raise HTTPException(422, '规则格式无效') from None
        observations, health = sources.collect(get, os.getenv, store().read_certificates(), datetime.now(timezone.utc))
        rows = [row for row in observations.get(rule['metric'], []) if (not rule.get('resource_id') or rule['resource_id'] == row['id'])
                and (not rule.get('server_id') or rule['server_id'] == (row.get('server_id') or row['id']))]
        return {'items': [{**row, 'matches': engine.compare(rule, row['value']) if row['value'] is not None else None} for row in rows], 'sources': health}

    @app.get('/api/v1/analysis')
    def analysis(days: int = Query(default=30, ge=1, le=365)):
        now = datetime.now(timezone.utc)
        current = data()
        events = [engine.normalize(dict(row)) for row in current['events'] if (at := engine.parse_time(row.get('fired_at'))) and (now - at).total_seconds() <= days * 86400]
        ack, recovery = [], []
        for row in events:
            fired = engine.parse_time(row['fired_at'])
            for key, result in [('acked_at', ack), ('recovered_at', recovery)]:
                at = engine.parse_time(row.get(key))
                if at and at >= fired:
                    result.append((at - fired).total_seconds())
        jobs = [j for j in current.get('deliveries', []) if (at := engine.parse_time(j.get('created_at'))) and (now - at).total_seconds() <= days * 86400 and not j.get('dry_run') and j['status'] in {'accepted', 'failed', 'uncertain'}]
        return {'total': len(events), 'by_source': dict(Counter(row['source'] for row in events)),
                'by_severity': dict(Counter(row['severity'] for row in events)),
                'top_rules': Counter(row['rule_name'] for row in events).most_common(10),
                'mean_ack_sec': round(sum(ack) / len(ack)) if ack else None,
                'mean_recovery_sec': round(sum(recovery) / len(recovery)) if recovery else None,
                'ack_samples': len(ack), 'recovery_samples': len(recovery),
                'delivery_failure_rate': round(sum(j['status'] != 'accepted' for j in jobs) / len(jobs) * 100, 1) if jobs else None}

    @app.get('/api/v1/notification-preview/{event_id}')
    def notification_preview(event_id: str):
        return notify.message(event_in(data(), event_id), 'firing')

    @app.get('/api/v1/health/status')
    def evaluation_health():
        current = data()
        meta = current.get('meta', {})
        return {**engine.overview(current, datetime.now(timezone.utc)),
                'status': 'ok' if engine.fresh(meta.get('last_evaluated_at'), datetime.now(timezone.utc), 180) else 'degraded',
                'queue_depth': sum(j['status'] in {'queued', 'retry', 'sending'} for j in current.get('deliveries', []))}

    stop = threading.Event()
    thread = None

    @app.on_event('startup')
    def start_delivery_worker():
        nonlocal thread
        if os.getenv('ALERTS_DELIVERY_ENABLED', 'false').lower() not in {'true', '1', 'yes'}:
            return
        stop.clear()
        def run():
            while not stop.wait(5):
                try:
                    dispatch_once(store)
                except Exception:
                    import logging
                    logging.getLogger('opscenter.alerts').warning('notification worker failed')
        thread = threading.Thread(target=run, name='alerts-delivery', daemon=True)
        thread.start()

    @app.on_event('shutdown')
    def stop_delivery_worker():
        stop.set()
        if thread:
            thread.join(timeout=12)
