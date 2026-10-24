"""Adapters consume explicit observations; absence never implies recovery."""
from __future__ import annotations

from engine import fresh


def collect(get, env, certificates, now):
    result, health = {}, {}
    inventory = get(env('HOST_INVENTORY_INTERNAL_URL', ''), env('HOST_INVENTORY_INTERNAL_TOKEN_FILE', ''), '/api/v1/internal/hosts')
    metrics = get(env('HOST_MONITOR_INTERNAL_URL', ''), env('HOST_MONITOR_INTERNAL_TOKEN_FILE', ''), '/api/v1/internal/metrics/latest')
    ready = bool(inventory and inventory.get('status') in {'ok', 'degraded'} and not inventory.get('error') and fresh(inventory.get('collected_at'), now))
    health['host'] = {'status': 'ok' if ready else 'unavailable', 'observed_at': (inventory or {}).get('collected_at')}
    metrics_ready = bool(metrics and metrics.get('status', 'ok') in {'ok', 'degraded'} and not metrics.get('error') and fresh(metrics.get('collected_at'), now))
    health['metrics'] = {'status': 'ok' if metrics_ready else 'unavailable', 'observed_at': (metrics or {}).get('collected_at')}
    metric_hosts = {str(row.get('id')): row for row in (metrics or {}).get('items', [])}
    for host in inventory.get('items', []) if ready else []:
        sample = metric_hosts.get(str(host['id']), {})
        valid = bool(metrics and metrics.get('status', 'ok') in {'ok', 'degraded'} and not metrics.get('error') and fresh(sample.get('last_sample_at'), now, 120))
        base = {'id': str(host['id']), 'name': host.get('name', ''), 'source': 'host', 'server_id': str(host['id']), 'url': '/hosts'}
        values = {'server_status': host.get('status'), 'agent_status': host.get('agent_status')}
        values.update({key: value if valid else None for key, value in sample.get('metrics', {}).items()})
        for metric, value in values.items():
            at = inventory['collected_at'] if metric in {'server_status', 'agent_status'} else sample.get('last_sample_at')
            result.setdefault(metric, []).append({**base, 'value': value, 'observed_at': at})
    for row in certificates:
        if not row.get('enabled', True):
            continue
        valid = fresh(row.get('updated_at'), now, 28800)
        base = {'id': f"cert:{row['id']}", 'name': row['domain'], 'source': 'certificate', 'server_id': row.get('server_id'), 'observed_at': row.get('updated_at'), 'url': '/alerts'}
        result.setdefault('cert_days_left', []).append({**base, 'value': row.get('days_left') if valid and not row.get('last_error') else None})
        result.setdefault('cert_scan_failed', []).append({**base, 'value': int(bool(row.get('last_error'))) if valid else None})
    health['certificate'] = {'status': 'ok' if all(fresh(row.get('updated_at'), now, 28800) for row in certificates if row.get('enabled', True)) else 'degraded'}
    for source, prefix, path in (
        ('service', 'SERVICE_CATALOG', '/api/v1/internal/alert-targets'),
        ('k3s', 'K3S', '/api/v1/internal/alert-targets'),
        ('pve', 'PVE', '/api/v1/internal/alert-targets'),
        ('electricity', 'ELECTRICITY', '/api/v1/internal/alert-targets'),
    ):
        if not env(prefix + '_INTERNAL_URL', ''):
            health[source] = {'status': 'unconfigured'}
            continue
        payload = get(env(prefix + '_INTERNAL_URL', ''), env(prefix + '_INTERNAL_TOKEN_FILE', ''), path)
        at = (payload or {}).get('collected_at')
        valid = bool(payload and payload.get('status') in {'ok', 'degraded'} and not payload.get('error') and fresh(at, now, min(86400, max(60, int(payload.get('stale_after_seconds', 300))))))
        health[source] = {'status': 'ok' if valid else 'unavailable', 'observed_at': at}
        if valid:
            rows = payload.get('items', [])
            for row in rows:
                if not row.get('id') or not row.get('metric'):
                    continue
                result.setdefault(row['metric'], []).append({**row, 'source': source,
                    'value': row.get('value') if fresh(row.get('observed_at') or at, now, min(86400, max(60, int(row.get('stale_after_seconds', 300))))) and row.get('data_status', 'ok') == 'ok' else None,
                    'observed_at': row.get('observed_at') or at})
    if ready and metrics_ready:
        health['metrics']['status'] = 'degraded' if any(not fresh(metric_hosts.get(str(host['id']), {}).get('last_sample_at'), now, 120) for host in inventory['items']) else 'ok'
    result['source_unavailable'] = [
        {'id': key, 'name': key, 'source': 'datasource', 'value': int(value['status'] != 'ok'), 'observed_at': now.isoformat(), 'url': '/alerts'}
        for key, value in health.items() if value['status'] != 'unconfigured'
    ]
    return result, health
