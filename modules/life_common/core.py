from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import requests
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


def secret(name):
    return Path(os.environ[name + '_FILE']).read_text(encoding='utf-8').strip()


class Upstream:
    def __init__(self, name):
        self.name = name
        self.base = os.getenv('LIFE_UPSTREAM_URL', '').rstrip('/')

    def request(self, method, path, *, body=None, form=None, params=None, files=None, binary=False):
        if not self.base or not path.startswith('/') or '..' in path:
            raise HTTPException(503, '上游服务尚未配置')
        token = secret('LIFE_UPSTREAM_TOKEN')
        headers = {} if self.name == 'wallos' else {'Authorization': 'Bearer ' + token}
        if self.name == 'wallos':
            form = {**(form or {}), 'api_key': token}
            method = 'POST'
        try:
            with requests.Session() as session:
                session.trust_env = False
                with session.request(method, self.base + path, json=body, data=form, params=params,
                                     files=files, headers=headers, timeout=(3, 15),
                                     allow_redirects=False, stream=True) as r:
                    if r.status_code >= 400 or r.is_redirect:
                        status = 422 if r.status_code in {400, 409, 422} else 404 if r.status_code == 404 else 502
                        raise HTTPException(status, '上游拒绝此操作，请核对字段或稍后重试')
                    chunks, size = [], 0
                    for chunk in r.iter_content(65536):
                        size += len(chunk)
                        if size > 12 * 1024 * 1024:
                            raise HTTPException(502, '上游响应超过12MiB限制')
                        chunks.append(chunk)
                    content = b''.join(chunks)
                    if binary:
                        return content, r.headers.get('Content-Type', 'application/octet-stream')
                    data = json.loads(content) if content else {}
                    if isinstance(data, dict) and data.get('success') is False:
                        raise HTTPException(422, '上游未接受此操作，请核对字段')
                    return data
        except (requests.RequestException, ValueError, OSError):
            raise HTTPException(502, '上游服务暂不可用，写入结果需核对') from None

    def get(self, path, **kwargs):
        return self.request('GET', path, **kwargs)


class Store:
    def connect(self):
        return psycopg.connect(secret('MODULE_DATABASE_URL'), connect_timeout=4, row_factory=dict_row)

    def migrate(self):
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS life_operations (
              id UUID PRIMARY KEY, action TEXT NOT NULL, digest TEXT NOT NULL,
              state TEXT NOT NULL, result JSONB, actor TEXT NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
            db.execute('''CREATE TABLE IF NOT EXISTS life_preferences (
              key TEXT PRIMARY KEY, value JSONB NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS life_deliveries (
              id TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
              occurrence TEXT NOT NULL, title TEXT NOT NULL, due_at TIMESTAMPTZ NOT NULL,
              state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
              retry_at TIMESTAMPTZ, acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
              snoozed_until TIMESTAMPTZ, last_error TEXT NOT NULL DEFAULT '',
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')

    def once(self, key, action, payload, actor, operation):
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        with self.connect() as db:
            inserted = db.execute('''INSERT INTO life_operations (id,action,digest,state,actor)
              VALUES (%s,%s,%s,'pending',%s) ON CONFLICT DO NOTHING RETURNING id''',
                                  (key, action, digest, actor)).fetchone()
            if not inserted:
                old = db.execute('SELECT * FROM life_operations WHERE id=%s', (key,)).fetchone()
                if old['action'] != action or old['digest'] != digest:
                    raise HTTPException(409, '操作标识已用于其他内容')
                if old['state'] == 'done':
                    return old['result']
                raise HTTPException(409, '上次操作结果待核对，请刷新确认后再操作')
        result = operation()
        with self.connect() as db:
            db.execute("UPDATE life_operations SET state='done',result=%s WHERE id=%s",
                       (Jsonb(result), key))
        return result

    def preference(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM life_preferences WHERE key=%s', (key,)).fetchone()
            return row['value'] if row else default

    def preferences(self, prefix):
        with self.connect() as db:
            rows = db.execute('SELECT key,value FROM life_preferences WHERE key LIKE %s', (prefix + '%',)).fetchall()
        return {row['key']: row['value'] for row in rows}

    @contextmanager
    def target_lock(self, target):
        number = int.from_bytes(hashlib.sha256(target.encode()).digest()[:8], 'big', signed=True)
        with self.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(%s)', (number,))
            yield

    def set_preference(self, key, value):
        with self.connect() as db:
            db.execute('''INSERT INTO life_preferences VALUES (%s,%s)
              ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value''', (key, Jsonb(value)))


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def check_revision(current, expected, field):
    if expected and str(current.get(field, '')) != expected:
        raise HTTPException(409, '资料已更新，请刷新后重新编辑')


def uuid_value(value: UUID):
    return str(value)


def date_value(value):
    if not value or str(value).startswith('0001-'):
        return ''
    return str(value)[:10]


def summary(data):
    return {'status': 'ok', 'collected_at': timestamp(), 'stale_after_sec': 300, 'data': data}
