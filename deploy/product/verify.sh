#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
test -f "$ROOT/backend/app/main.py"
test -f "$ROOT/backend/requirements.txt"
test -f "$ROOT/frontend-vite/dist/index.html"
test -f "$ROOT/agent/opsagent.py"
bash -n "$ROOT/deploy/product/install.sh" "$ROOT/deploy/product/backup.sh" "$ROOT/deploy/product/restore.sh" "$ROOT/deploy/product/migration-smoke.sh"
python3 -m compileall -q "$ROOT/backend/app" "$ROOT/agent/opsagent.py"
POSTGRES_PASSWORD=verify docker compose -f "$ROOT/deploy/product/postgres.compose.yml" config -q
python3 -c 'import json,runpy,sys; sys.exit(0 if json.load(open(sys.argv[2]))["version"] == runpy.run_path(sys.argv[1])["VERSION"] else "frontend/backend version mismatch")' "$ROOT/backend/app/version.py" "$ROOT/frontend-vite/dist/release.json"
echo "Portable bundle static verification passed"
