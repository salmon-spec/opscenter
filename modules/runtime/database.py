"""Check the dedicated database assigned to a module."""

import os
from pathlib import Path

import psycopg


def database_ready():
    path = Path(os.environ["MODULE_DATABASE_URL_FILE"])
    with psycopg.connect(path.read_text(encoding="utf-8").strip(), connect_timeout=3) as db:
        db.execute("SELECT 1")
