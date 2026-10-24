"""Deterministic alert transitions and transactional notification planning."""
from __future__ import annotations

import hashlib
import math
import os
import uuid
from datetime import datetime, timedelta, timezone


def parse_time(value):
    try:
        value = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def fresh(value, now, seconds=300):
    at = parse_time(value)
    return bool(at and -30 <= (now - at).total_seconds() <= seconds)


def normalize(event):
    event.setdefault('source', 'certificate' if str(event.get('server_id', '')).startswith('cert:') else 'host')
    event.setdefault('resource_id', event.get('server_id', ''))
    event.setdefault('resource_name', event.get('server_name', ''))
    event.setdefault('severity', 'warning')
    event.setdefault('handling_status', 'acked' if event.get('acked_at') or event.get('status') == 'acked' else 'unacked')
    event['condition_status'] = 'firing' if event.get('status') == 'acked' else event.get('status', 'pending')
    event.setdefault('timeline', [])
    return event


def compare(rule, value, recovery=False):
    if value is None:
        return False
    try:
        threshold = rule.get('recovery_threshold') if recovery and rule.get('recovery_threshold') is not None else rule['threshold']
        left, right = (float(value), float(threshold)) if rule.get('value_type', 'numeric') == 'numeric' else (str(value), str(threshold))
        if isinstance(left, float) and (not math.isfinite(left) or not math.isfinite(right)):
            return False
        return {'>': lambda: left > right, '<': lambda: left < right, '>=': lambda: left >= right,
                '<=': lambda: left <= right, '==': lambda: left == right, '!=': lambda: left != right}[rule.get('operator', '>')]()
    except (ValueError, TypeError, KeyError):
        return False


def stamp(event, action, now, actor='system', message=''):
    event.setdefault('timeline', []).append({'action': action, 'at': now.isoformat(), 'actor': actor, 'message': message})


def silenced(event, silences, now):
    for item in silences:
        if item.get('ended_at'):
            continue
        if item.get('rule_id') and item['rule_id'] != event.get('rule_id'):
            continue
        if item.get('server_id') and item['server_id'] != event.get('server_id'):
            continue
        if item.get('resource_id') and item['resource_id'] != event.get('resource_id'):
            continue
        start, end = parse_time(item.get('starts_at')), parse_time(item.get('ends_at'))
        if not start or not end or not start <= now < end:
            continue
        if item.get('weekdays'):
            local = now.astimezone(timezone(timedelta(hours=8)))
            minute = local.hour * 60 + local.minute
            begin, finish = item.get('daily_start', 0), item.get('daily_end', 1440)
            day = local.weekday()
            if begin > finish and minute < finish:
                day = (day - 1) % 7
            if day not in item['weekdays'] or not (begin <= minute < finish if begin < finish else minute >= begin or minute < finish):
                continue
        return item.get('reason') or '维护静默'
    return ''


def transition(data, rule, target, now):
    """A missing observation preserves the incident and resets continuity timers."""
    fingerprint = hashlib.sha256(f"{rule['id']}|{target['source']}|{target['id']}|{target.get('subresource_id', '')}".encode()).hexdigest()
    for row in data['events']:
        normalize(row)
    event = next((row for row in reversed(data['events']) if row.get('fingerprint') == fingerprint and row['condition_status'] in {'pending', 'firing'}), None)
    if event is None and target['source'] == 'host':
        event = next((row for row in reversed(data['events']) if not row.get('fingerprint') and row.get('rule_id') == rule['id'] and row.get('server_id') == target['id'] and row['condition_status'] in {'pending', 'firing'}), None)
        if event:
            event['fingerprint'] = fingerprint
    value = target.get('value')
    if value is not None and rule.get('value_type', 'numeric') == 'numeric':
        try:
            if not math.isfinite(float(value)):
                value = None
        except (ValueError, TypeError):
            value = None
    if value is None:
        if event:
            event.update(data_status='unknown', recovery_started_at=None)
            if event['condition_status'] == 'pending':
                event['first_breached_at'] = now.isoformat()
        return
    breach = compare(rule, value, recovery=bool(event and event['condition_status'] == 'firing'))
    if not event and breach:
        event = {'id': str(uuid.uuid4()), 'fingerprint': fingerprint, 'rule_id': rule['id'], 'rule_name': rule['name'],
                 'source': target['source'], 'resource_id': target['id'], 'resource_name': target['name'],
                 'subresource_id': target.get('subresource_id', ''), 'server_id': target.get('server_id') or target['id'],
                 'server_name': target['name'], 'severity': rule.get('severity', 'warning'), 'metric': rule['metric'],
                 'threshold': rule['threshold'], 'operator': rule.get('operator', '>'), 'status': 'pending',
                 'handling_status': 'unacked', 'first_breached_at': now.isoformat(), 'created_at': now.isoformat(),
                 'occurrences': 0, 'transition_seq': 0, 'timeline': [], 'url': target.get('url', '')}
        event['runbook_url'] = rule.get('runbook_url', '')
        event['notification_owner'] = target.get('notification_owner', 'alerts')
        data['events'].append(event)
        stamp(event, 'pending', now)
    if event is None:
        return
    normalize(event)
    event.update(current_value=str(value), last_seen_at=now.isoformat(), data_status='ok', observed_at=target.get('observed_at', now.isoformat()))
    if breach:
        event['recovery_started_at'] = None
        if target.get('observed_at') != event.get('last_counted_at'):
            event['occurrences'] = event.get('occurrences', 0) + 1
            event['last_counted_at'] = target.get('observed_at')
        start = parse_time(event.get('first_breached_at')) or now
        if event['condition_status'] == 'pending' and (now - start).total_seconds() >= rule.get('duration_sec', 60):
            event.update(status='firing', condition_status='firing', fired_at=now.isoformat(), transition_seq=event.get('transition_seq', 0) + 1)
            stamp(event, 'firing', now)
    elif event['condition_status'] == 'pending':
        event.update(status='cancelled', condition_status='cancelled', recovered_at=now.isoformat())
        stamp(event, 'cancelled', now)
    else:
        event.setdefault('recovery_started_at', None)
        event['recovery_started_at'] = event['recovery_started_at'] or now.isoformat()
        start = parse_time(event['recovery_started_at'])
        if (now - start).total_seconds() >= rule.get('recovery_sec', 0):
            event.update(status='recovered', condition_status='recovered', recovered_at=now.isoformat(), transition_seq=event.get('transition_seq', 0) + 1)
            stamp(event, 'recovered', now)


def plan_notifications(data, now):
    deliveries = data.setdefault('deliveries', [])
    policies = data.setdefault('policies', [])
    rules = {row['id']: row for row in data['rules']}
    jobs_by_event = {}
    for job in deliveries:
        jobs_by_event.setdefault((job.get('event_id'), job.get('policy_id')), []).append(job)
    keys = {job['id'] for job in deliveries}
    for event in data['events']:
        normalize(event)
        rule = rules.get(event.get('rule_id'), {})
        event['suppressed_reason'] = silenced(event, data['silences'], now)
        if not event['suppressed_reason'] and rule.get('inhibit_on_host_down') and event.get('source') == 'service':
            parent = next((row for row in data['events'] if row.get('source') == 'host' and row.get('metric') == 'server_status' and row.get('server_id') == event.get('server_id') and row.get('condition_status') == 'firing'), None)
            if parent:
                event['suppressed_reason'] = f"关联主机故障：{parent.get('server_name', '')}"
        if event.get('notification_eligible') is False or event['condition_status'] not in {'firing', 'recovered'} or not event.get('fired_at'):
            continue
        for policy in policies:
            if not policy.get('enabled') or policy.get('archived'):
                continue
            if event.get('notification_owner', 'alerts') != 'alerts' and policy['channel'] != 'inapp':
                continue
            if rule.get('policy_ids') and policy['id'] not in rule['policy_ids']:
                continue
            if not rule.get('enabled', True) or rule.get('archived'):
                continue
            if policy.get('sources') and event['source'] not in policy['sources']:
                continue
            if policy.get('severities') and event['severity'] not in policy['severities']:
                continue
            previous = jobs_by_event.get((event['id'], policy['id']), [])
            recovery = event['condition_status'] == 'recovered'
            if recovery:
                if not policy.get('notify_recovery', True) or not any(j['kind'] != 'recovered' and j['status'] == 'accepted' for j in previous):
                    continue
            elif event.get('data_status') != 'ok' or event['suppressed_reason'] or event['handling_status'] != 'unacked':
                continue
            if not recovery and (now - (parse_time(event.get('fired_at')) or now)).total_seconds() < policy.get('escalate_after_sec', 0):
                continue
            quiet_start, quiet_end = policy.get('quiet_start'), policy.get('quiet_end')
            local = now.astimezone(timezone(timedelta(hours=8)))
            minute = local.hour * 60 + local.minute
            if quiet_start is not None and quiet_end is not None and (quiet_start <= minute < quiet_end if quiet_start < quiet_end else minute >= quiet_start or minute < quiet_end):
                continue
            firing_jobs = [j for j in previous if j['kind'] != 'recovered']
            if recovery:
                kind, round_no = 'recovered', 0
            else:
                if any(j['status'] in {'queued', 'retry', 'sending', 'uncertain', 'failed'} for j in firing_jobs):
                    continue
                limit = policy.get('max_notifications', 0)
                if limit and len(firing_jobs) >= limit:
                    continue
                interval = policy.get('repeat_sec', 3600)
                last = max((parse_time(j['created_at']) for j in firing_jobs), default=None)
                if last and (not interval or (now - last).total_seconds() < interval):
                    continue
                kind, round_no = 'firing', len(firing_jobs)
            key = hashlib.sha256(f"{event['id']}|{policy['id']}|{kind}|{round_no}".encode()).hexdigest()
            if key in keys:
                continue
            deliveries.append({'id': key, 'event_id': event['id'], 'policy_id': policy['id'], 'policy_name': policy['name'],
                               'channel': policy['channel'], 'kind': kind, 'status': 'queued', 'attempts': 0,
                               'created_at': now.isoformat(), 'next_attempt_at': (now + timedelta(seconds=policy.get('group_wait_sec', 0))).isoformat(), 'dry_run': policy.get('dry_run', True)})
            keys.add(key)


def overview(data, now):
    events = [normalize(dict(row)) for row in data['events']]
    active = [e for e in events if e['condition_status'] == 'firing']
    today = now.astimezone(timezone(timedelta(hours=8))).date()
    return {'active': len(active), 'unacked': sum(e['handling_status'] == 'unacked' for e in active),
            'critical': sum(e['severity'] == 'critical' for e in active),
            'today': sum(bool((at := parse_time(e.get('fired_at'))) and at.astimezone(timezone(timedelta(hours=8))).date() == today) for e in events),
            'delivery_failed': sum(j['status'] in {'failed', 'uncertain'} for j in data.get('deliveries', [])),
            'pending': sum(e['condition_status'] == 'pending' for e in events),
            'delivery_enabled': os.getenv('ALERTS_DELIVERY_ENABLED', 'false').lower() in {'true', '1', 'yes'},
            'last_evaluated_at': data.get('meta', {}).get('last_evaluated_at'),
            'sources': data.get('meta', {}).get('sources', {})}
