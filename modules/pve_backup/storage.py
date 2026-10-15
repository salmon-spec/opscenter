from __future__ import annotations

import json
import os
from pathlib import Path


class PveStore:
    def __init__(self):
        self.path = os.getenv("PVE_BACKUP_DATABASE_URL_FILE", "")

    def configured(self) -> bool:
        return bool(self.path and Path(self.path).is_file())

    def connect(self):
        import psycopg
        from psycopg.rows import dict_row

        with open(self.path, encoding="utf-8") as secret:
            url = secret.read().strip()
        return psycopg.connect(url, row_factory=dict_row)

    def migrate(self):
        with self.connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS pve_snapshots (
                    id SMALLINT PRIMARY KEY CHECK (id = 1),
                    payload JSONB NOT NULL,
                    collected_at TIMESTAMPTZ NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS pve_recovery_evidence (
                    vmid TEXT PRIMARY KEY,
                    level TEXT NOT NULL CHECK (level IN
                        ('archive_verified', 'isolated_boot', 'business_verified')),
                    verified_at TIMESTAMPTZ,
                    verified_on DATE,
                    source TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT ''
                )
            """)
            db.execute("ALTER TABLE pve_recovery_evidence ALTER COLUMN verified_at DROP NOT NULL")
            db.execute("ALTER TABLE pve_recovery_evidence ADD COLUMN IF NOT EXISTS verified_on DATE")
            db.execute("""
                CREATE TABLE IF NOT EXISTS pve_backup_objects (
                    object_name TEXT PRIMARY KEY,
                    vmid TEXT NOT NULL,
                    size_bytes BIGINT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    observed_at TIMESTAMPTZ NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS pve_oss_manifest_state (
                    id SMALLINT PRIMARY KEY CHECK (id = 1),
                    collected_at TIMESTAMPTZ NOT NULL
                )
            """)

    def ping(self):
        with self.connect() as db:
            db.execute("SELECT 1")

    def read(self):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM pve_snapshots WHERE id=1").fetchone()
        return row["payload"] if row else None

    def save(self, payload: dict):
        with self.connect() as db:
            db.execute("""
                INSERT INTO pve_snapshots (id, payload, collected_at)
                VALUES (1, %s::jsonb, %s)
                ON CONFLICT (id) DO UPDATE SET
                    payload=EXCLUDED.payload, collected_at=EXCLUDED.collected_at
            """, (json.dumps(payload, ensure_ascii=False, default=str), payload["collected_at"]))

    def evidence(self) -> dict[str, dict]:
        with self.connect() as db:
            rows = db.execute("SELECT vmid, level, verified_at, verified_on, source, note FROM pve_recovery_evidence").fetchall()
        return {row["vmid"]: {
            "level": row["level"], "source": row["source"], "note": row["note"],
            "verified_at": (row["verified_at"] or row["verified_on"]).isoformat(),
        } for row in rows}

    def set_evidence(self, *, vmid: str, level: str, verified_on, source: str, note: str):
        with self.connect() as db:
            db.execute("""
                INSERT INTO pve_recovery_evidence
                    (vmid, level, verified_on, verified_at, source, note)
                VALUES (%s, %s, %s, NULL, %s, %s)
                ON CONFLICT (vmid) DO UPDATE SET
                    level=EXCLUDED.level, verified_on=EXCLUDED.verified_on,
                    verified_at=NULL, source=EXCLUDED.source, note=EXCLUDED.note
            """, (vmid, level, verified_on, source, note))

    def oss_manifest(self):
        with self.connect() as db:
            state = db.execute("SELECT collected_at FROM pve_oss_manifest_state WHERE id=1").fetchone()
            if not state:
                return [], None
            rows = db.execute("""
                SELECT object_name, vmid, size_bytes, created_at
                FROM pve_backup_objects ORDER BY created_at DESC
            """).fetchall()
        return [{"vmid": row["vmid"], "node": "", "storage": "OSS",
                 "volid": row["object_name"], "size_bytes": row["size_bytes"],
                 "created_at": row["created_at"], "format": "vma.zst", "source": "oss"}
                for row in rows], state["collected_at"]

    def replace_oss_manifest(self, objects: list[dict], collected_at):
        with self.connect() as db:
            db.execute("DELETE FROM pve_backup_objects")
            with db.cursor() as cursor:
                cursor.executemany("""
                    INSERT INTO pve_backup_objects
                        (object_name, vmid, size_bytes, created_at, observed_at)
                    VALUES (%(object_name)s, %(vmid)s, %(size_bytes)s, %(created_at)s, %(observed_at)s)
                """, [{**item, "observed_at": collected_at} for item in objects])
            db.execute("""
                INSERT INTO pve_oss_manifest_state (id, collected_at) VALUES (1, %s)
                ON CONFLICT (id) DO UPDATE SET collected_at=EXCLUDED.collected_at
            """, (collected_at,))
