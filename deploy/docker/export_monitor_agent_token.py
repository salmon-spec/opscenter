"""One-time VM100 test cutover: stage the existing Agent token without printing it."""
import os
import sys
from pathlib import Path
from uuid import UUID

import psycopg

host_id = str(UUID(sys.argv[1])) if len(sys.argv) == 2 else None
if not host_id:
    raise SystemExit("expected host UUID")
target = Path("/tmp/opscenter-host-monitor-agent-token")
if target.exists():
    raise SystemExit("staging file exists")
database_url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
with psycopg.connect(database_url) as db:
    rows = db.execute("SELECT agent_token FROM servers WHERE id=%s", (host_id,)).fetchall()
if len(rows) != 1 or not rows[0][0]:
    raise SystemExit("exactly one configured Agent token required")
target.write_text(rows[0][0] + "\n", encoding="utf-8")
target.chmod(0o600)
print("Agent token staged without disclosure")
