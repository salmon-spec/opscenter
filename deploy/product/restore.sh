#!/usr/bin/env bash
set -euo pipefail

: "${BACKUP_PASSWORD:?set BACKUP_PASSWORD}"
: "${CONFIRM_RESTORE:?set CONFIRM_RESTORE=YES}"
[ "$CONFIRM_RESTORE" = YES ] || { echo "CONFIRM_RESTORE must be YES" >&2; exit 1; }
[ $# -eq 1 ] || { echo "Usage: BACKUP_PASSWORD=... CONFIRM_RESTORE=YES sudo -E $0 backup.tar.gz.enc" >&2; exit 1; }

APP_DIR=${OPSCENTER_HOME:-/opt/opscenter}
MUTABLE_DIR=${OPSCENTER_MUTABLE_DIR:-"$APP_DIR/frontend"}
SECRETS_FILE=${OPSCENTER_SECRETS_FILE:-/etc/opscenter/secrets.env}
COMPOSE_FILE=${COMPOSE_FILE:-"$APP_DIR/deploy/product/postgres.compose.yml"}
SERVICE_CONTROL=${SERVICE_CONTROL:-true}
HEALTH_URL=${HEALTH_URL:-http://127.0.0.1:9091/openapi.json}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSWORD -in "$1" | tar -C "$work" -xzf -
[ -s "$work/database/opscenter.dump" ]
[ -s "$work/config/secrets.env" ]

env_value() { sed -n "s/^$1=//p" "$work/config/secrets.env" | tail -1; }
POSTGRES_DB=$(env_value POSTGRES_DB)
POSTGRES_USER=$(env_value POSTGRES_USER)
POSTGRES_PASSWORD=$(env_value POSTGRES_PASSWORD)
POSTGRES_DB=${POSTGRES_DB:-opscenter}
POSTGRES_USER=${POSTGRES_USER:-opscenter}
[ -n "$POSTGRES_PASSWORD" ] || { echo "backup does not contain POSTGRES_PASSWORD" >&2; exit 1; }
case "$POSTGRES_USER" in *[!a-zA-Z0-9_]*) echo "invalid POSTGRES_USER" >&2; exit 1;; esac
LOKI_DATA_DIR=$(env_value LOKI_DATA_DIR)
LOKI_DATA_DIR=${LOKI_DATA_DIR:-/opt/opscenter-data/loki}

[ "$SERVICE_CONTROL" = true ] && systemctl stop opscenter-backend 2>/dev/null || true
docker compose --env-file "$work/config/secrets.env" -f "$COMPOSE_FILE" up -d db
for _ in $(seq 1 60); do
  docker compose --env-file "$work/config/secrets.env" -f "$COMPOSE_FILE" exec -T db psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "select 1" >/dev/null 2>&1 && break
  sleep 2
done
printf "ALTER ROLE \"%s\" PASSWORD :'restored_password';\n" "$POSTGRES_USER" | \
  docker compose --env-file "$work/config/secrets.env" -f "$COMPOSE_FILE" exec -T db \
    psql -U "$POSTGRES_USER" -d postgres -v restored_password="$POSTGRES_PASSWORD" >/dev/null
docker compose --env-file "$work/config/secrets.env" -f "$COMPOSE_FILE" exec -T db \
  pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-privileges \
  <"$work/database/opscenter.dump"
install -m 0600 "$work/config/secrets.env" "$SECRETS_FILE"
for file in groups.json services.json; do
  [ -f "$work/mutable/$file" ] && install -m 0644 "$work/mutable/$file" "$MUTABLE_DIR/$file"
done
if [ -f "$work/database/loki-data.tar.gz" ]; then
  install -d -m 0755 "$LOKI_DATA_DIR"
  tar -C "$LOKI_DATA_DIR" -xzf "$work/database/loki-data.tar.gz"
fi
[ "$SERVICE_CONTROL" = true ] && systemctl restart opscenter-backend
[ "$SERVICE_CONTROL" = true ] && curl --retry 20 --retry-delay 2 --retry-connrefused -fsS "$HEALTH_URL" >/dev/null
echo "Restore completed"
