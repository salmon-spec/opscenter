"""Encrypted destinations and bounded, DNS-pinned notification delivery."""
from __future__ import annotations

import html
import http.client
import ipaddress
import json
import os
import socket
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet


def cipher():
    path = os.getenv('ALERTS_CREDENTIAL_KEY_FILE', '/run/secrets/alerts_credential_key')
    return Fernet(Path(path).read_bytes().strip())


def seal(value):
    return cipher().encrypt(json.dumps(value).encode()).decode()


def unseal(value):
    return json.loads(cipher().decrypt(value.encode()))


def destination(value, resolve=True):
    parsed = urlsplit(value)
    allowed = {v.strip().lower() for v in os.getenv('ALERTS_WEBHOOK_ALLOWED_HOSTS', '').split(',') if v.strip()}
    host = (parsed.hostname or '').lower()
    if not host or parsed.username or parsed.password or parsed.fragment or len(value) > 2048:
        raise ValueError('通知地址格式无效')
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and host in allowed):
        raise ValueError('通知地址必须使用 HTTPS')
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    targets = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM) if resolve else []
    if resolve and (not targets or (host not in allowed and any(not ipaddress.ip_address(row[4][0]).is_global for row in targets))):
        raise ValueError('通知地址不在允许的网络范围')
    return parsed, targets


def post_json(url, payload, token='', request_id='', extra_headers=None):
    parsed, targets = destination(url)
    host, port = parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)
    body = json.dumps(payload, ensure_ascii=False).encode()
    if len(body) > 32768:
        raise ValueError('通知内容过长')
    # Resolve once, connect to that exact address, and retain TLS hostname validation.
    family, socktype, proto, _, sockaddr = targets[0]
    sock = socket.socket(family, socktype, proto)
    sock.settimeout(8)
    conn = http.client.HTTPConnection(host, port, timeout=8)
    try:
        sock.connect(sockaddr)
        conn.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host) if parsed.scheme == 'https' else sock
        headers = {'Content-Type': 'application/json', 'X-OpsCenter-Delivery-ID': request_id}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        headers.update(extra_headers or {})
        conn.request('POST', (parsed.path or '/') + ('?' + parsed.query if parsed.query else ''), body, headers)
        response = conn.getresponse()
        raw = response.read(32769)
        if len(raw) > 32768:
            raise ValueError('通知响应过长')
        return response.status, raw
    finally:
        conn.close()
        sock.close()


def message(event, kind):
    status = '已恢复' if kind == 'recovered' else '故障告警'
    at = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    source = {'host': '主机', 'service': '服务', 'certificate': '证书', 'k3s': 'K3s', 'pve': 'PVE', 'electricity': '电费', 'datasource': '数据源'}.get(event.get('source'), '告警')
    severity = {'critical': '严重', 'warning': '警告', 'info': '提示'}.get(event.get('severity'), '警告')
    title = f"{status} · {event.get('rule_name', '告警')}"
    text = f"{source}：{event.get('resource_name') or event.get('server_name', '-')}\n级别：{severity}\n当前值：{event.get('current_value', '-')}\n条件：{event.get('operator', '')} {event.get('threshold', '')}\n时间：{at}（北京时间）\n事件：{event['id']}"
    base = os.getenv('ALERTS_PUBLIC_URL', '').rstrip('/')
    if base:
        text += f"\n详情：{base}/#/alerts?event={event['id']}"
    return {'title': title[:200], 'text': text[:4000], 'event_id': event['id'], 'kind': kind}


def send(policy, event, job):
    content = job.get('group_content') or message(event, job['kind'])
    if policy['channel'] == 'inapp':
        return 'accepted', ''
    secret = unseal(policy['destination_cipher'])
    if policy['channel'] == 'pushplus':
        status, raw = post_json('https://www.pushplus.plus/send', {
            'token': secret['token'], 'title': content['title'],
            'content': '<pre>' + html.escape(content['text']) + '</pre>', 'template': 'html',
        }, request_id=job['id'])
        if 200 <= status < 300:
            try:
                return ('accepted', '') if json.loads(raw).get('code') == 200 else ('failed', '平台拒绝通知，请检查渠道配置')
            except (ValueError, AttributeError):
                return 'uncertain', '平台响应格式无效，请核对平台记录'
    elif policy['channel'] == 'sms':
        base = os.getenv('SMS_INTERNAL_URL', '').rstrip('/')
        if not base:
            return 'failed', '短信模块尚未接入'
        status, raw = post_json(base + '/api/v1/internal/send', {'phone': secret['phone'], 'message': content['title'] + '\n' + content['text']},
                                request_id=job['id'], extra_headers={'X-SMS-Token': secret['token']})
        if 200 <= status < 300:
            try:
                return ('accepted', '') if json.loads(raw).get('ok') is True else ('failed', '短信模块拒绝通知')
            except (ValueError, AttributeError):
                return 'uncertain', '短信模块响应无效，请核对发送记录'
    else:
        status, _ = post_json(secret['url'], content, secret.get('token', ''), job['id'])
    if 200 <= status < 300:
        return 'accepted', ''
    return ('retry' if status == 429 or status >= 500 else 'failed'), f'平台返回 HTTP {status}'
