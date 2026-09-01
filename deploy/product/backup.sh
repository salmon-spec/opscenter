#!/usr/bin/env bash
set -euo pipefail

: "${BACKUP_PASSWORD:?set BACKUP_PASSWORD for AES-256 encrypted backup}"
APP_DIR=${OPSCENTER_HOME:-/opt/opscenter}
SECRETS_FILE=${OPSCENTER_SECRETS_FILE:-/etc/opscenter/secrets.env}
DB_CONTAINER=${DB_CONTAINER:-opscenter-postgres}
LOKI_CONTAINER=${LOKI_CONTAINER:-opscenter-loki}
OUTPUT=${1:-"opscenter-migration-$(date +%Y%m%d-%H%M%S).tar.gz.enc"}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

install -d "$work/database" "$work/config" "$work/mutable"
docker exec "$DB_CONTAINER" sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' >"$work/database/opscenter.dump"
cp "$SECRETS_FILE" "$work/config/secrets.env"
for live_config in /etc/systemd/system/opscenter-backend.service /etc/caddy/Caddyfile; do
  [ -f "$live_config" ] && cp "$live_config" "$work/config/$(basename "$live_config").live"
done
for config_dir in ${EXTRA_CONFIG_PATHS:-}; do
  [ -d "$config_dir" ] && cp -a "$config_dir" "$work/config/$(basename "$config_dir")"
done
for key in POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD; do
  grep -q "^${key}=" "$work/config/secrets.env" || printf '%s=%s\n' "$key" "$(docker exec "$DB_CONTAINER" printenv "$key")" >>"$work/config/secrets.env"
done
for file in groups.json services.json; do
  [ -f "$APP_DIR/frontend/$file" ] && cp "$APP_DIR/frontend/$file" "$work/mutable/$file"
done
if docker inspect "$LOKI_CONTAINER" >/dev/null 2>&1; then
  docker exec "$LOKI_CONTAINER" tar -C /loki -czf - . >"$work/database/loki-data.tar.gz"
fi
printf 'created_at=%s\nversion=%s\n' "$(date -Iseconds)" "$(cat "$APP_DIR/backend/app/version.py" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)" >"$work/metadata.txt"
tar -C "$work" -czf - . | openssl enc -aes-256-cbc -salt -pbkdf2 -pass env:BACKUP_PASSWORD -out "$OUTPUT"
chmod 0600 "$OUTPUT"
echo "$OUTPUT"
