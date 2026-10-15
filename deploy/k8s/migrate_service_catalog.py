"""One-time production service catalog data copy; run inside the old backend pod."""

import base64
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from cryptography.fernet import Fernet
from psycopg.rows import dict_row


def main():
    source_url = os.environ["DATABASE_URL"].replace("+psycopg", "")
    target_url = Path("/tmp/service_catalog_url").read_text().strip()
    new_key = Path("/tmp/service_catalog_key").read_bytes().strip()
    old_key = base64.urlsafe_b64encode(hashlib.sha256(os.environ["CREDENTIAL_KEY"].strip().encode()).digest())
    old_cipher, new_cipher = Fernet(old_key), Fernet(new_key)
    with psycopg.connect(source_url, row_factory=dict_row) as old, psycopg.connect(target_url, row_factory=dict_row) as new:
        tables = ("service_profile_overrides", "service_preferences", "service_credentials", "credential_access")
        assert all(new.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0 for table in tables), "target must be empty"
        existing_probes = new.execute("SELECT count(*) AS n FROM service_probe_results").fetchone()["n"]
        profiles = old.execute("SELECT * FROM plaza_service_profiles").fetchall()
        prefs = old.execute("SELECT * FROM plaza_service_preferences").fetchall()
        credentials = old.execute("SELECT * FROM plaza_service_credentials").fetchall()
        access = old.execute("SELECT * FROM plaza_credential_access").fetchall()
        profile_fields = ("server_id", "name", "description", "category", "icon", "entry_url", "health_url", "login_notes", "documentation_url", "owner", "tags", "probe_enabled", "probe_interval_seconds", "probe_timeout_seconds", "probe_success_statuses", "probe_verify_tls", "probe_failure_threshold", "probe_recovery_threshold", "probe_notifications_enabled")
        for row in profiles:
            overrides = {key: str(row[key]) if key == "server_id" else row[key] for key in profile_fields if row[key] is not None}
            new.execute("INSERT INTO service_profile_overrides VALUES (%s,%s::jsonb,%s)", (row["plaza_key"], json.dumps(overrides, ensure_ascii=False), row["updated_at"]))
        preference_map = {row["catalog_key"]: (row["hidden"], None) for row in prefs}
        for row in profiles:
            if row["sort_order"] is not None:
                hidden = preference_map.get(row["plaza_key"], (False, None))[0]
                preference_map[row["plaza_key"]] = (hidden, row["sort_order"])
        for key, (hidden, order) in preference_map.items():
            new.execute("INSERT INTO service_preferences VALUES (%s,%s,%s)", (key, hidden, order))
        by_key = {}
        for row in credentials:
            plaintext = old_cipher.decrypt(row["secret_ciphertext"].encode()).decode() if row["secret_ciphertext"] else ""
            ciphertext = new_cipher.encrypt(plaintext.encode()).decode() if plaintext else None
            assert not plaintext or new_cipher.decrypt(ciphertext.encode()).decode() == plaintext
            by_key.setdefault(row["plaza_key"], []).append((row["username"], plaintext))
            new.execute("""INSERT INTO service_credentials
                (id,service_key,label,username,secret_ciphertext,notes,is_default,sort_order,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (row["id"], row["plaza_key"], row["label"], row["username"], ciphertext,
                 row["notes"] or "", row["is_default"], row["sort_order"], row["created_at"], row["updated_at"]))
        extra = 0
        for row in profiles:
            if not row["secret_ciphertext"]:
                continue
            plaintext = old_cipher.decrypt(row["secret_ciphertext"].encode()).decode()
            if (row["username"] or "", plaintext) in by_key.get(row["plaza_key"], []):
                continue
            ciphertext = new_cipher.encrypt(plaintext.encode()).decode()
            new.execute("""INSERT INTO service_credentials
                (id,service_key,label,username,secret_ciphertext,notes,is_default,sort_order,created_at,updated_at)
                VALUES (%s,%s,'旧版账号',%s,%s,%s,%s,0,%s,%s)""",
                (uuid4(), row["plaza_key"], row["username"] or "", ciphertext,
                 row["login_notes"] or "", not by_key.get(row["plaza_key"]), row["created_at"], row["updated_at"]))
            extra += 1
        for row in access:
            new.execute("INSERT INTO credential_access VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (row["id"], row["plaza_key"], row["credential_id"], row["actor"] or "", row["ip"], row["action"], row["created_at"]))
        with old.cursor() as cur:
            cur.execute("SELECT id,plaza_key,status,http_status,latency_ms,checked_at FROM plaza_probe_results ORDER BY checked_at")
            new.execute("CREATE TEMP TABLE imported_probes (LIKE service_probe_results) ON COMMIT DROP")
            with new.cursor() as dst:
                with dst.copy("COPY imported_probes (id,service_key,status,http_status,latency_ms,error,probe_url,checked_at) FROM STDIN") as copy:
                    count = 0
                    for row in cur:
                        copy.write_row((row["id"], row["plaza_key"], row["status"], row["http_status"],
                                        round(row["latency_ms"]) if row["latency_ms"] is not None else None,
                                        "", "", row["checked_at"]))
                        count += 1
        new.execute("INSERT INTO service_probe_results SELECT * FROM imported_probes ON CONFLICT (id) DO NOTHING")
        expected = {"service_profile_overrides": len(profiles), "service_preferences": len(preference_map),
                    "service_credentials": len(credentials) + extra, "credential_access": len(access), "service_probe_results": count + existing_probes}
        assert all(new.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == n for table, n in expected.items()), "count mismatch"
        new.commit()
        print(json.dumps(expected, sort_keys=True))


if __name__ == "__main__":
    main()
