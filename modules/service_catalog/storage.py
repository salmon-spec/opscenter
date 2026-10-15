from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from cryptography.fernet import Fernet


def _credential_fernet():
    path = os.getenv("SERVICE_CATALOG_CREDENTIAL_KEY_FILE", "/run/secrets/service_catalog_credential_key")
    with open(path, "rb") as secret:
        return Fernet(secret.read().strip())


class CatalogStore:
    def connect(self):
        import psycopg
        from psycopg.rows import dict_row

        url = os.getenv("SERVICE_CATALOG_DATABASE_URL", "")
        if not url:
            path = os.getenv("SERVICE_CATALOG_DATABASE_URL_FILE", "/run/secrets/service_catalog_db_url")
            with open(path, encoding="utf-8") as secret:
                url = secret.read().strip()
        return psycopg.connect(url, row_factory=dict_row)

    def migrate(self):
        with self.connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS catalog_snapshots (
                    snapshot_key TEXT PRIMARY KEY,
                    payload JSONB NOT NULL,
                    collected_at TIMESTAMPTZ NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS manual_services (
                    id TEXT PRIMARY KEY, server_id TEXT NOT NULL, server_name TEXT NOT NULL,
                    server_host TEXT NOT NULL, name TEXT NOT NULL, url TEXT NOT NULL,
                    category TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                    health_path TEXT NOT NULL DEFAULT '', pinned BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    archived_at TIMESTAMPTZ
                )
            """)
            db.execute("ALTER TABLE manual_services ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ")
            db.execute("""
                CREATE TABLE IF NOT EXISTS service_preferences (
                    service_key TEXT PRIMARY KEY,
                    hidden BOOLEAN NOT NULL DEFAULT FALSE,
                    sort_order INTEGER
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS service_profile_overrides (
                    service_key TEXT PRIMARY KEY,
                    overrides JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS service_credentials (
                    id UUID PRIMARY KEY, service_key TEXT NOT NULL, label TEXT NOT NULL,
                    username TEXT NOT NULL DEFAULT '', secret_ciphertext TEXT,
                    notes TEXT NOT NULL DEFAULT '', is_default BOOLEAN NOT NULL DEFAULT FALSE,
                    sort_order INTEGER NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS ix_service_credentials_key ON service_credentials(service_key, is_default DESC, sort_order, created_at)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS service_probe_results (
                    id UUID PRIMARY KEY, service_key TEXT NOT NULL, status TEXT NOT NULL,
                    http_status INTEGER, latency_ms INTEGER, error TEXT NOT NULL DEFAULT '',
                    probe_url TEXT NOT NULL, checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS ix_service_probe_key_time ON service_probe_results(service_key, checked_at DESC)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS credential_access (
                    id UUID PRIMARY KEY, service_key TEXT NOT NULL, credential_id UUID,
                    actor TEXT NOT NULL, ip TEXT, action TEXT NOT NULL DEFAULT 'reveal',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)

    def ping(self):
        with self.connect() as db:
            db.execute("SELECT 1")

    def read_snapshot(self, key: str):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload, collected_at FROM catalog_snapshots WHERE snapshot_key = %s",
                (key,),
            ).fetchone()
        if not row:
            return None
        collected_at = row["collected_at"]
        if collected_at.tzinfo is None:
            collected_at = collected_at.replace(tzinfo=timezone.utc)
        return row["payload"], collected_at

    def save_snapshot(self, key: str, payload, collected_at: datetime):
        with self.connect() as db:
            db.execute("""
                INSERT INTO catalog_snapshots (snapshot_key, payload, collected_at)
                VALUES (%s, %s::jsonb, %s)
                ON CONFLICT (snapshot_key) DO UPDATE SET
                    payload = EXCLUDED.payload, collected_at = EXCLUDED.collected_at
            """, (key, json.dumps(payload, ensure_ascii=False), collected_at))

    def expire_snapshot(self, key: str):
        with self.connect() as db:
            db.execute("UPDATE catalog_snapshots SET collected_at=to_timestamp(0) WHERE snapshot_key=%s", (key,))

    def list_manual_services(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM manual_services WHERE archived_at IS NULL ORDER BY pinned DESC, name").fetchall()
        return [{
            "id": f"manual:{row['id']}", "service_id": row["id"], "key": f"manual:{row['id']}",
            "name": row["name"], "url": row["url"], "entry_url": row["url"],
            "category": row["category"], "description": row["description"],
            "server_id": row["server_id"], "server_name": row["server_name"],
            "server_host": row["server_host"], "source": "manual", "kind": "manual",
            "status": "unknown", "status_checked_at": None, "stale": False,
            "has_credentials": False, "pinned": row["pinned"], "health_path": row["health_path"],
        } for row in rows]

    def create_manual_service(self, service: dict, actor: str):
        import json

        with self.connect() as db:
            db.execute("""
                INSERT INTO manual_services
                    (id,server_id,server_name,server_host,name,url,category,description,health_path,pinned)
                VALUES (%(id)s,%(server_id)s,%(server_name)s,%(server_host)s,%(name)s,%(url)s,
                    %(category)s,%(description)s,%(health_path)s,%(pinned)s)
            """, service)
            db.execute("""
                INSERT INTO module_audit (actor,action,target,details)
                VALUES (%s,'service.create',%s,%s::jsonb)
            """, (actor, service["id"], json.dumps({k: v for k, v in service.items() if k != "id"}, ensure_ascii=False)))

    def preferences(self):
        with self.connect() as db:
            rows = db.execute("SELECT service_key,hidden,sort_order FROM service_preferences").fetchall()
        return {row["service_key"]: row for row in rows}

    def profile_overrides(self):
        with self.connect() as db:
            rows = db.execute("SELECT service_key,overrides,updated_at FROM service_profile_overrides").fetchall()
        return {row["service_key"]: {**row["overrides"], "profile_updated_at": row["updated_at"].isoformat()} for row in rows}

    def update_profile(self, key: str, values: dict, actor: str):
        with self.connect() as db:
            row = db.execute(
                "SELECT overrides FROM service_profile_overrides WHERE service_key=%s FOR UPDATE", (key,),
            ).fetchone()
            before = row["overrides"] if row else {}
            after = dict(before)
            for field, value in values.items():
                if value is None:
                    after.pop(field, None)
                else:
                    after[field] = value
            for field in ("entry_url", "health_url", "documentation_url"):
                if after.get(field) == "":
                    after.pop(field)
            db.execute("""
                INSERT INTO service_profile_overrides (service_key,overrides,updated_at)
                VALUES (%s,%s::jsonb,now())
                ON CONFLICT (service_key) DO UPDATE SET
                    overrides=EXCLUDED.overrides,updated_at=EXCLUDED.updated_at
            """, (key, json.dumps(after, ensure_ascii=False)))
            db.execute("""
                INSERT INTO module_audit (actor,action,target,details)
                VALUES (%s,'service.profile.update',%s,%s::jsonb)
            """, (actor, key, json.dumps({"before": before, "after": after}, ensure_ascii=False)))

    def set_visibility(self, key: str, hidden: bool, actor: str):
        with self.connect() as db:
            db.execute("""
                INSERT INTO service_preferences (service_key,hidden) VALUES (%s,%s)
                ON CONFLICT (service_key) DO UPDATE SET hidden=EXCLUDED.hidden
            """, (key, hidden))
            db.execute("""
                INSERT INTO module_audit (actor,action,target,details)
                VALUES (%s,'service.visibility',%s,jsonb_build_object('hidden',%s))
            """, (actor, key, hidden))

    def set_order(self, keys: list[str], actor: str):
        with self.connect() as db:
            for index, key in enumerate(keys):
                db.execute("""
                    INSERT INTO service_preferences (service_key,sort_order) VALUES (%s,%s)
                    ON CONFLICT (service_key) DO UPDATE SET sort_order=EXCLUDED.sort_order
                """, (key, index))
            db.execute("""
                INSERT INTO module_audit (actor,action,target,details)
                VALUES (%s,'service.order','catalog',jsonb_build_object('keys',%s::jsonb))
            """, (actor, json.dumps(keys)))

    def update_manual_service(self, service_id: str, values: dict, actor: str) -> bool:
        with self.connect() as db:
            before = db.execute("SELECT * FROM manual_services WHERE id=%s AND archived_at IS NULL FOR UPDATE", (service_id,)).fetchone()
            if not before:
                return False
            db.execute("""
                UPDATE manual_services SET server_id=%(server_id)s,server_name=%(server_name)s,
                    server_host=%(server_host)s,name=%(name)s,url=%(url)s,category=%(category)s,
                    description=%(description)s,health_path=%(health_path)s,pinned=%(pinned)s
                WHERE id=%(id)s
            """, {**values, "id": service_id})
            db.execute("""
                INSERT INTO module_audit (actor,action,target,details)
                VALUES (%s,'service.update',%s,%s::jsonb)
            """, (actor, service_id, json.dumps({"before": {key: before[key] for key in values}, "after": values}, default=str, ensure_ascii=False)))
            return True

    def archive_manual_service(self, service_id: str, actor: str) -> bool:
        with self.connect() as db:
            row = db.execute("UPDATE manual_services SET archived_at=now() WHERE id=%s AND archived_at IS NULL RETURNING name", (service_id,)).fetchone()
            if not row:
                return False
            db.execute("""
                INSERT INTO module_audit (actor,action,target,details)
                VALUES (%s,'service.archive',%s,jsonb_build_object('name',%s::text))
            """, (actor, service_id, row["name"]))
            return True

    @staticmethod
    def _credential(row):
        return {
            "id": str(row["id"]), "label": row["label"], "username": row["username"],
            "notes": row["notes"], "is_default": row["is_default"],
            "sort_order": row["sort_order"], "has_password": bool(row["secret_ciphertext"]),
            "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat(),
        }

    def list_credentials(self, service_key: str):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM service_credentials WHERE service_key=%s ORDER BY is_default DESC,sort_order,created_at", (service_key,)).fetchall()
        return [self._credential(row) for row in rows]

    def credential_counts(self):
        with self.connect() as db:
            rows = db.execute("SELECT service_key,count(*) AS count,bool_or(secret_ciphertext IS NOT NULL AND secret_ciphertext <> '') AS has_password FROM service_credentials GROUP BY service_key").fetchall()
        return {row["service_key"]: {"credential_count": row["count"], "has_credentials": row["has_password"]} for row in rows}

    def create_credential(self, service_key: str, values: dict, actor: str):
        with self.connect() as db:
            rows = db.execute("SELECT id,is_default,sort_order FROM service_credentials WHERE service_key=%s FOR UPDATE", (service_key,)).fetchall()
            if len(rows) >= 20:
                raise ValueError("每个服务最多保存 20 组凭证")
            make_default = values["is_default"] or not rows
            if make_default:
                db.execute("UPDATE service_credentials SET is_default=FALSE WHERE service_key=%s", (service_key,))
            credential_id = uuid.uuid4()
            ciphertext = _credential_fernet().encrypt(values["password"].encode()).decode() if values["password"] else None
            row = db.execute("""
                INSERT INTO service_credentials(id,service_key,label,username,secret_ciphertext,notes,is_default,sort_order)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
            """, (credential_id, service_key, values["label"], values["username"], ciphertext, values["notes"], make_default,
                  max((row["sort_order"] for row in rows), default=-1) + 1)).fetchone()
            db.execute("INSERT INTO module_audit(actor,action,target,details) VALUES(%s,'credential.create',%s,%s::jsonb)",
                       (actor, str(credential_id), json.dumps({"service_key": service_key, "label": values["label"]}, ensure_ascii=False)))
        return self._credential(row)

    def update_credential(self, service_key: str, credential_id: str, values: dict, actor: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM service_credentials WHERE id=%s AND service_key=%s FOR UPDATE", (credential_id, service_key)).fetchone()
            if not row:
                return None
            rows = db.execute("SELECT id FROM service_credentials WHERE service_key=%s ORDER BY is_default DESC,sort_order,created_at FOR UPDATE", (service_key,)).fetchall()
            if values.get("is_default") is True:
                db.execute("UPDATE service_credentials SET is_default=(id=%s) WHERE service_key=%s", (credential_id, service_key))
            elif values.get("is_default") is False and row["is_default"]:
                replacement = next((item for item in rows if str(item["id"]) != credential_id), None)
                if replacement is None:
                    raise ValueError("服务至少需要保留一个默认凭证")
                db.execute("UPDATE service_credentials SET is_default=(id=%s) WHERE service_key=%s", (replacement["id"], service_key))
            columns = {key: value for key, value in values.items() if key in {"label", "username", "notes"}}
            if values.get("clear_password"):
                columns["secret_ciphertext"] = None
            elif values.get("password"):
                columns["secret_ciphertext"] = _credential_fernet().encrypt(values["password"].encode()).decode()
            assignments = [f"{key}=%s" for key in columns]
            if assignments:
                db.execute(f"UPDATE service_credentials SET {','.join(assignments)},updated_at=now() WHERE id=%s", (*columns.values(), credential_id))
            if "is_default" in values:
                db.execute("UPDATE service_credentials SET is_default=%s,updated_at=now() WHERE id=%s", (values["is_default"], credential_id))
            updated = db.execute("SELECT * FROM service_credentials WHERE id=%s", (credential_id,)).fetchone()
            db.execute("INSERT INTO module_audit(actor,action,target,details) VALUES(%s,'credential.update',%s,%s::jsonb)",
                       (actor, credential_id, json.dumps({"service_key": service_key, "fields": sorted(values)}, ensure_ascii=False)))
        return self._credential(updated)

    def delete_credential(self, service_key: str, credential_id: str, actor: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM service_credentials WHERE id=%s AND service_key=%s FOR UPDATE", (credential_id, service_key)).fetchone()
            if not row:
                return False
            if row["is_default"]:
                replacement = db.execute("SELECT id FROM service_credentials WHERE service_key=%s AND id<>%s ORDER BY sort_order,created_at LIMIT 1 FOR UPDATE", (service_key, credential_id)).fetchone()
                if replacement:
                    db.execute("UPDATE service_credentials SET is_default=TRUE WHERE id=%s", (replacement["id"],))
            db.execute("DELETE FROM service_credentials WHERE id=%s", (credential_id,))
            db.execute("INSERT INTO module_audit(actor,action,target,details) VALUES(%s,'credential.delete',%s,%s::jsonb)",
                       (actor, credential_id, json.dumps({"service_key": service_key, "label": row["label"]}, ensure_ascii=False)))
        return True

    def reveal_credential(self, service_key: str, credential_id: str | None, actor: str, ip: str | None):
        with self.connect() as db:
            if credential_id:
                row = db.execute("SELECT * FROM service_credentials WHERE id=%s AND service_key=%s", (credential_id, service_key)).fetchone()
            else:
                row = db.execute("SELECT * FROM service_credentials WHERE service_key=%s ORDER BY is_default DESC,sort_order,created_at LIMIT 1", (service_key,)).fetchone()
            if not row or not row["secret_ciphertext"]:
                return None
            password = _credential_fernet().decrypt(row["secret_ciphertext"].encode()).decode()
            access_id = uuid.uuid4()
            db.execute("INSERT INTO credential_access(id,service_key,credential_id,actor,ip) VALUES(%s,%s,%s,%s,%s)",
                       (access_id, service_key, row["id"], actor, ip))
            return {"id": str(row["id"]), "username": row["username"], "password": password}

    def credential_history(self, service_key: str, limit: int):
        with self.connect() as db:
            rows = db.execute("SELECT id,action,actor,credential_id,ip,created_at FROM credential_access WHERE service_key=%s ORDER BY created_at DESC LIMIT %s", (service_key, limit)).fetchall()
        return [{**row, "id": str(row["id"]), "credential_id": str(row["credential_id"]) if row["credential_id"] else None,
                 "actor": row["actor"] or "-", "ip": row["ip"] or "-", "created_at": row["created_at"].isoformat()} for row in rows]

    def save_probe_result(self, service_key: str, result: dict):
        with self.connect() as db:
            db.execute("""
                INSERT INTO service_probe_results
                    (id,service_key,status,http_status,latency_ms,error,probe_url,checked_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
            """, (uuid.uuid4(), service_key, result["status"], result.get("http_status"),
                  result.get("latency_ms"), result.get("error", ""), result["probe_url"], result["checked_at"]))

    def probe_history(self, service_key: str, hours: int = 24, limit: int = 20):
        with self.connect() as db:
            rows = db.execute("""
                SELECT id,checked_at,status,http_status,latency_ms,error,probe_url
                FROM service_probe_results
                WHERE service_key=%s AND checked_at >= now() - (%s * interval '1 hour')
                ORDER BY checked_at DESC LIMIT %s
            """, (service_key, hours, limit)).fetchall()
        return [{**row, "id": str(row["id"]), "checked_at": row["checked_at"].isoformat(),
                 "error_code": ""} for row in reversed(rows)]

    def probe_summaries(self, hours: int = 24):
        with self.connect() as db:
            rows = db.execute("""
                SELECT service_key, count(*) AS checks,
                    round(100.0 * count(*) FILTER (WHERE status='up') / count(*), 2) AS uptime_percent,
                    round(avg(latency_ms), 1) AS avg_latency_ms,
                    (array_agg(status ORDER BY checked_at DESC))[1] AS status,
                    max(checked_at) AS checked_at, min(checked_at) AS first_checked_at
                FROM service_probe_results
                WHERE checked_at >= now() - (%s * interval '1 hour')
                GROUP BY service_key
            """, (hours,)).fetchall()
        return {row["service_key"]: row for row in rows}
