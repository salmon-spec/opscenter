"""One-time test DB provisioning inside the backend container; never prints credentials.

Run with a new module name, then copy /tmp/opscenter-<name>-db-url out with
`docker cp` into a root-only Compose secret. Refuses to overwrite an existing DB.
"""
import os
import re
import secrets
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


name = sys.argv[1] if len(sys.argv) == 2 else ""
if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", name):
    raise SystemExit("module database name required")
target = Path(f"/tmp/opscenter-{name}-db-url")
if target.exists():
    raise SystemExit("credential staging file already exists")
settings = conninfo_to_dict(os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1))
settings["dbname"] = "postgres"
password = secrets.token_hex(32)
with psycopg.connect(**settings, autocommit=True) as db:
    if db.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)).fetchone():
        raise SystemExit("role already exists")
    if db.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
        raise SystemExit("database already exists")
    db.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION").format(
        sql.Identifier(name), sql.Literal(password)))
    db.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(name)))
    db.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
target.write_text(f"postgresql://{name}:{password}@db:5432/{name}\n", encoding="utf-8")
target.chmod(0o600)
print(f"provisioned:{name}")
