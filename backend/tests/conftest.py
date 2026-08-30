"""Shared test bootstrap so the complete suite uses one isolated database."""

import os
from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://opscenter:opscenter123@127.0.0.1:5433/opscenter_test",
)
os.environ.setdefault("OPS_AUTH_ENABLED", "false")
os.environ.setdefault("LOCAL_HOST", "127.0.0.1")
