"""Rewrite legacy SSH credentials with the configured key. Back up the DB first.

Run with the backend environment: python scripts/encrypt_host_credentials.py
No credentials or connection strings are printed. All rows commit together.
"""
from pathlib import Path
import sys
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.orm.attributes import flag_modified
from app.credential_crypto import SSHCredential, _fernet
from app.database import get_db
from app.models import Server


def migrate():
    _fernet()  # Validate key before any database writes.
    with get_db() as db:
        lock = " FOR UPDATE" if db.bind.dialect.name == "postgresql" else ""
        ids = db.execute(text("SELECT id FROM servers WHERE ssh_key IS NOT NULL AND ssh_key <> '' AND ssh_key NOT LIKE :prefix" + lock),
                         {"prefix": SSHCredential.prefix + "%"}).scalars().all()
        count = 0
        for identifier in ids:
            server = db.get(Server, uuid.UUID(str(identifier)))
            flag_modified(server, "ssh_key")
            count += 1
        db.commit()
    return count


if __name__ == "__main__":
    print(f"Encrypted legacy host credentials: {migrate()}")
