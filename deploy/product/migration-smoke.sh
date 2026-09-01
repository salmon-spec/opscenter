#!/usr/bin/env bash
set -euo pipefail

: "${BACKUP_PASSWORD:?set BACKUP_PASSWORD}"
[ $# -eq 1 ] || { echo "Usage: BACKUP_PASSWORD=... $0 production-migration.tar.gz.enc" >&2; exit 1; }

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PYTHON=${VERIFY_PYTHON:-$ROOT/venv/bin/python3}
[ -x "$PYTHON" ] || PYTHON=$(command -v python3)
DB_PORT=${VERIFY_DB_PORT:-55432}
API_PORT=${VERIFY_API_PORT:-19091}
WEB_PORT=${VERIFY_WEB_PORT:-18081}
container="opscenter-portable-test-db-$$"
work=$(mktemp -d -p "${VERIFY_WORK_ROOT:-/tmp}" opscenter-portable-test.XXXXXX)
backend_pid=
web_pid=
cleanup() {
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "migration smoke failed; PostgreSQL log follows" >&2
    docker logs --tail 80 "$container" 2>&1 || true
    [ ! -f "$work/backend.log" ] || tail -80 "$work/backend.log" >&2
  fi
  [ -z "$backend_pid" ] || kill "$backend_pid" 2>/dev/null || true
  [ -z "$web_pid" ] || kill "$web_pid" 2>/dev/null || true
  docker rm -f "$container" >/dev/null 2>&1 || true
  rm -rf "$work"
  exit "$rc"
}
trap cleanup EXIT

openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSWORD -in "$1" | tar -C "$work" -xzf -
test -s "$work/database/opscenter.dump"
credential_key=$(sed -n 's/^CREDENTIAL_KEY=//p' "$work/config/secrets.env" | tail -1)
onepanel_entry=$(sed -n 's/^OPSCENTER_1PANEL_ENTRY_URL=//p' "$work/config/secrets.env" | tail -1)
[ -n "$credential_key" ]

test_password=$(openssl rand -hex 16)
docker run -d --name "$container" --label opscenter.portability-test=true \
  -e POSTGRES_DB=opscenter -e POSTGRES_USER=opscenter -e POSTGRES_PASSWORD="$test_password" \
  -p "127.0.0.1:${DB_PORT}:5432" postgres:16-alpine >/dev/null
for _ in $(seq 1 60); do
  docker exec "$container" psql -h 127.0.0.1 -U opscenter -d opscenter -Atqc 'select 1' >/dev/null 2>&1 && break
  sleep 2
done
docker exec "$container" psql -h 127.0.0.1 -U opscenter -d opscenter -Atqc 'select 1' >/dev/null
docker exec -i "$container" pg_restore -U opscenter -d opscenter --clean --if-exists --no-owner --no-privileges <"$work/database/opscenter.dump"

(
  cd "$ROOT/backend"
  exec env DATABASE_URL="postgresql+psycopg://opscenter:${test_password}@127.0.0.1:${DB_PORT}/opscenter" \
    CREDENTIAL_KEY="$credential_key" OPSCENTER_1PANEL_ENTRY_URL="$onepanel_entry" \
    PREVIEW_MODE=true OPS_AUTH_ENABLED=false LOKI_URL= \
    "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" >"$work/backend.log" 2>&1
) &
backend_pid=$!
caddy file-server --root "$ROOT/frontend-vite/dist" --listen ":$WEB_PORT" >"$work/web.log" 2>&1 &
web_pid=$!
for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:${API_PORT}/openapi.json" >/dev/null 2>&1 && break
  sleep 2
done

version=$(curl -fsS "http://127.0.0.1:${API_PORT}/openapi.json" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["info"]["version"])')
servers=$(curl -fsS "http://127.0.0.1:${API_PORT}/api/v2/servers" | "$PYTHON" -c 'import json,sys; print(len(json.load(sys.stdin)))')
plaza=$(curl -fsS "http://127.0.0.1:${API_PORT}/api/v2/services/plaza" | "$PYTHON" -c 'import json,sys; print(len(json.load(sys.stdin)))')
tables=$(docker exec "$container" psql -U opscenter -d opscenter -Atqc "select count(*) from information_schema.tables where table_schema='public'")
for _ in $(seq 1 100); do
  curl -o /dev/null -sS -w '%{time_total}\n' "http://127.0.0.1:${API_PORT}/api/v2/servers" >>"$work/times"
done
p50=$(sort -n "$work/times" | awk 'NR==50 {print $1}')
p95=$(sort -n "$work/times" | awk 'NR==95 {print $1}')
curl -fsS "http://127.0.0.1:${WEB_PORT}/" >/dev/null

[ "$version" = 4.7.0 ]
[ "$tables" -gt 0 ]
[ "$servers" -gt 0 ]
[ "$plaza" -gt 0 ]
printf 'migration_smoke=passed version=%s tables=%s servers=%s plaza=%s p50_seconds=%s p95_seconds=%s\n' \
  "$version" "$tables" "$servers" "$plaza" "$p50" "$p95"
