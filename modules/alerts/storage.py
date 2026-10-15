from __future__ import annotations

import json
import os
from datetime import timezone


class AlertsStore:
    def connect(self):
        import psycopg
        from psycopg.rows import dict_row

        url = os.getenv("ALERTS_DATABASE_URL", "")
        if not url:
            path = os.getenv("ALERTS_DATABASE_URL_FILE", "/run/secrets/alerts_db_url")
            with open(path, encoding="utf-8") as secret:
                url = secret.read().strip()
        return psycopg.connect(url, row_factory=dict_row)

    def migrate(self):
        with self.connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS alert_snapshots (
                    id SMALLINT PRIMARY KEY CHECK (id = 1),
                    payload JSONB NOT NULL,
                    collected_at TIMESTAMPTZ NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS alert_certificates (
                    id SMALLINT PRIMARY KEY CHECK (id = 1),
                    payload JSONB NOT NULL
                )
            """)
            db.execute("INSERT INTO alert_certificates (id,payload) VALUES (1,'[]'::jsonb) ON CONFLICT (id) DO NOTHING")

    def ping(self):
        with self.connect() as db:
            db.execute("SELECT 1")

    def read(self):
        with self.connect() as db:
            row = db.execute("SELECT payload, collected_at FROM alert_snapshots WHERE id=1").fetchone()
        if not row:
            return None
        collected_at = row["collected_at"]
        if collected_at.tzinfo is None:
            collected_at = collected_at.replace(tzinfo=timezone.utc)
        return row["payload"], collected_at

    def save(self, payload: dict, collected_at):
        with self.connect() as db:
            db.execute("""
                INSERT INTO alert_snapshots (id, payload, collected_at)
                VALUES (1, %s::jsonb, %s)
                ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload, collected_at=EXCLUDED.collected_at
            """, (json.dumps(payload, ensure_ascii=False), collected_at))

    def mutate(self, payload: dict, actor: str, action: str, target: str):
        with self.connect() as db:
            row = db.execute("SELECT id FROM alert_snapshots WHERE id=1 FOR UPDATE").fetchone()
            if row:
                db.execute("UPDATE alert_snapshots SET payload=%s::jsonb,collected_at=now() WHERE id=1",
                           (json.dumps(payload, ensure_ascii=False),))
            else:
                db.execute("INSERT INTO alert_snapshots (id,payload,collected_at) VALUES (1,%s::jsonb,now())",
                           (json.dumps(payload, ensure_ascii=False),))
            db.execute("""
                INSERT INTO module_audit (actor,action,target,details)
                VALUES (%s,%s,%s,'{}'::jsonb)
            """, (actor, action, target))

    def read_certificates(self):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM alert_certificates WHERE id=1").fetchone()
        return row["payload"] if row else []

    def mutate_certificates(self, mutate, actor: str | None = None, action: str | None = None, target: str = ""):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM alert_certificates WHERE id=1 FOR UPDATE").fetchone()
            payload = row["payload"] if row else []
            mutate(payload)
            db.execute("""
                INSERT INTO alert_certificates (id,payload) VALUES (1,%s::jsonb)
                ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload
            """, (json.dumps(payload, ensure_ascii=False),))
            if actor and action:
                db.execute("""
                    INSERT INTO module_audit (actor,action,target,details)
                    VALUES (%s,%s,%s,'{}'::jsonb)
                """, (actor, action, target))
