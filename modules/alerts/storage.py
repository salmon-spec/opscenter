from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone

COLLECTIONS = ('events', 'rules', 'silences', 'policies', 'deliveries', 'notes')


def legacy_event(row, rules, certificates):
    row = copy.deepcopy(row)
    rule = next((item for item in rules if item.get('id') == row.get('rule_id')), {})
    row['notification_eligible'] = False
    for key in ('metric', 'threshold', 'operator', 'severity'):
        if key in rule:
            row.setdefault(key, rule[key])
    if rule.get('metric') == 'cert_days_left':
        row['source'] = 'certificate'
        matches = [c for c in certificates if str(row.get('server_id')) == f"cert:{c['id']}"
                   or (row.get('server_name') == c.get('domain') and (not row.get('server_id') or str(row['server_id']) == str(c.get('server_id'))))]
        if len(matches) == 1:
            row['resource_id'] = f"cert:{matches[0]['id']}"
            row['resource_name'] = matches[0]['domain']
            row['fingerprint'] = hashlib.sha256(f"{rule['id']}|certificate|{row['resource_id']}|".encode()).hexdigest()
        else:
            row['data_status'] = 'unknown'
            row['migration_note'] = '历史证书事件缺少唯一目标标识，保留原记录'
    return row


class AlertsStore:
    def connect(self):
        import psycopg
        from psycopg.rows import dict_row
        url = os.getenv('ALERTS_DATABASE_URL', '')
        if not url:
            with open(os.getenv('ALERTS_DATABASE_URL_FILE', '/run/secrets/alerts_db_url'), encoding='utf-8') as secret:
                url = secret.read().strip()
        return psycopg.connect(url, row_factory=dict_row)

    @staticmethod
    def _lock(db):
        # Serialize short state transitions across workers; network IO stays outside this lock.
        db.execute('SELECT pg_advisory_xact_lock(173528, 1)')

    def migrate(self):
        with self.connect() as db:
            self._lock(db)
            db.execute('CREATE TABLE IF NOT EXISTS alert_snapshots (id SMALLINT PRIMARY KEY CHECK(id=1), payload JSONB NOT NULL, collected_at TIMESTAMPTZ NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS alert_certificates (id SMALLINT PRIMARY KEY CHECK(id=1), payload JSONB NOT NULL)')
            db.execute("INSERT INTO alert_certificates VALUES (1,'[]'::jsonb) ON CONFLICT DO NOTHING")
            db.execute('CREATE TABLE IF NOT EXISTS alert_center_meta (id SMALLINT PRIMARY KEY CHECK(id=1), payload JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())')
            for name in COLLECTIONS:
                db.execute(f'CREATE TABLE IF NOT EXISTS alert_center_{name} (id TEXT PRIMARY KEY, payload JSONB NOT NULL)')
            db.execute("CREATE INDEX IF NOT EXISTS alert_center_delivery_due ON alert_center_deliveries ((payload->>'status'), (payload->>'next_attempt_at'))")
            db.execute("CREATE INDEX IF NOT EXISTS alert_center_event_status ON alert_center_events ((payload->>'status'), (payload->>'created_at'))")
            db.execute("CREATE TABLE IF NOT EXISTS module_audit (id BIGSERIAL PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, details JSONB NOT NULL DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ NOT NULL DEFAULT now())")
            if not db.execute('SELECT id FROM alert_center_meta WHERE id=1').fetchone():
                old = db.execute('SELECT payload FROM alert_snapshots WHERE id=1').fetchone()
                certs = db.execute('SELECT payload FROM alert_certificates WHERE id=1').fetchone()['payload']
                for name in ('events', 'rules', 'silences'):
                    for row in (old['payload'].get(name, []) if old else []):
                        if name == 'events':
                            row = legacy_event(row, old['payload'].get('rules', []), certs)
                        self._put(db, name, row)
                # Preserve the legacy snapshot for explicit rollback/export, without dual writes.
                db.execute('INSERT INTO alert_center_meta(id,payload) VALUES(1,%s::jsonb)',
                           (json.dumps({'initialized': bool(old), 'schema_version': 1}),))

    @staticmethod
    def _put(db, name, row):
        db.execute(f'INSERT INTO alert_center_{name}(id,payload) VALUES(%s,%s::jsonb) ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload',
                   (row['id'], json.dumps(row, ensure_ascii=False)))

    @staticmethod
    def _read(db):
        data = {name: [row['payload'] for row in db.execute(f'SELECT payload FROM alert_center_{name} ORDER BY id').fetchall()] for name in COLLECTIONS}
        meta = db.execute('SELECT payload,updated_at FROM alert_center_meta WHERE id=1').fetchone()
        data['meta'] = meta['payload'] if meta else {}
        return data, meta['updated_at'] if meta else datetime.now(timezone.utc)

    def read(self):
        with self.connect() as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            data, at = self._read(db)
            return (data, at) if data['meta'].get('initialized') else None

    def save(self, payload, collected_at):
        def initialize(data):
            if data['meta'].get('initialized'):
                return False
            for name in ('events', 'rules', 'silences'):
                data[name] = payload.get(name, [])
                if name == 'events':
                    data[name] = [legacy_event(row, payload.get('rules', []), []) for row in data[name]]
            data['meta']['initialized'] = True
        self.mutate(initialize, 'system:migration', 'alert.snapshot.import', 'legacy')

    def mutate(self, fn, actor: str, action: str, target: str):
        with self.connect() as db:
            self._lock(db)
            data, _ = self._read(db)
            before = copy.deepcopy(data)
            if fn(data) is False:
                return data
            data['meta']['initialized'] = True
            if data == before:
                return data
            for name in COLLECTIONS:
                old = {row['id']: row for row in before[name]}
                new = {row['id']: row for row in data.get(name, [])}
                for key in old.keys() - new.keys():
                    db.execute(f'DELETE FROM alert_center_{name} WHERE id=%s', (key,))
                for key, row in new.items():
                    if old.get(key) != row:
                        self._put(db, name, row)
            db.execute('UPDATE alert_center_meta SET payload=%s::jsonb,updated_at=now() WHERE id=1', (json.dumps(data['meta']),))
            if actor and action:
                db.execute('INSERT INTO module_audit(actor,action,target) VALUES(%s,%s,%s)', (actor, action, target))
            return data

    def ping(self):
        with self.connect() as db:
            db.execute('SELECT 1')

    def read_certificates(self):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM alert_certificates WHERE id=1').fetchone()
            return row['payload'] if row else []

    def mutate_certificates(self, mutate, actor=None, action=None, target=''):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM alert_certificates WHERE id=1 FOR UPDATE').fetchone()
            payload = row['payload'] if row else []
            mutate(payload)
            db.execute('UPDATE alert_certificates SET payload=%s::jsonb WHERE id=1', (json.dumps(payload, ensure_ascii=False),))
            if actor and action:
                db.execute('INSERT INTO module_audit(actor,action,target) VALUES(%s,%s,%s)', (actor, action, target))
