#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo ./deploy/product/install.sh" >&2
  exit 1
fi

SOURCE_DIR=$(cd "$(dirname "$0")/../.." && pwd)
APP_DIR=${OPSCENTER_HOME:-/opt/opscenter}
SECRETS_FILE=${OPSCENTER_SECRETS_FILE:-/etc/opscenter/secrets.env}
COMPOSE_FILE="$APP_DIR/deploy/product/postgres.compose.yml"

for command in python3 docker caddy curl openssl rsync; do
  command -v "$command" >/dev/null || { echo "Missing prerequisite: $command" >&2; exit 1; }
done
docker compose version >/dev/null

install -d -m 0755 "$APP_DIR" /etc/opscenter /opt/opscenter-data/postgres
if [ "$SOURCE_DIR" != "$APP_DIR" ]; then
  rsync -a --delete \
    --exclude=.git --exclude=.venv --exclude=venv --exclude=node_modules \
    --exclude=__pycache__ --exclude=.pytest_cache --exclude='*-test.db' \
    --exclude=backup --exclude='backup_*' \
    "$SOURCE_DIR/" "$APP_DIR/"
fi

if [ ! -f "$SECRETS_FILE" ]; then
  db_password=$(openssl rand -hex 24)
  admin_password=$(openssl rand -base64 24 | tr -d '\n')
  jwt_secret=$(openssl rand -hex 32)
  credential_key=$(openssl rand -hex 32)
  agent_token=$(openssl rand -hex 32)
  cat >"$SECRETS_FILE" <<EOF
POSTGRES_DB=opscenter
POSTGRES_USER=opscenter
POSTGRES_PASSWORD=$db_password
POSTGRES_BIND_IP=127.0.0.1
POSTGRES_PORT=5433
POSTGRES_DATA_DIR=/opt/opscenter-data/postgres
DATABASE_URL=postgresql+psycopg://opscenter:$db_password@127.0.0.1:5433/opscenter
OPS_AUTH_ENABLED=false
OPS_JWT_SECRET=$jwt_secret
OPS_ADMIN_USER=admin
OPS_ADMIN_PASSWORD=$admin_password
CREDENTIAL_KEY=$credential_key
LOCAL_AGENT_TOKEN=$agent_token
LOCAL_HOST=127.0.0.1
LOCAL_SERVER_NAME=OpsCenter
OPSCENTER_1PANEL_ENTRY_URL=
PREVIEW_MODE=false
LOKI_URL=
LOKI_RETENTION_DAYS=365
LOKI_DATA_DIR=/opt/opscenter-data/loki
EOF
  echo "Generated $SECRETS_FILE; record OPS_ADMIN_PASSWORD before leaving this host."
fi
chmod 0600 "$SECRETS_FILE"

docker compose --env-file "$SECRETS_FILE" -f "$COMPOSE_FILE" up -d
for _ in $(seq 1 60); do
  docker compose --env-file "$SECRETS_FILE" -f "$COMPOSE_FILE" exec -T db sh -c 'psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "select 1"' >/dev/null 2>&1 && break
  sleep 2
done
docker compose --env-file "$SECRETS_FILE" -f "$COMPOSE_FILE" exec -T db sh -c 'psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "select 1"' >/dev/null

python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --disable-pip-version-check -r "$APP_DIR/backend/requirements.txt"

if [ ! -f "$APP_DIR/frontend-vite/dist/index.html" ]; then
  echo "frontend-vite/dist is missing; build it before packaging." >&2
  exit 1
fi
install -d -m 0755 "$APP_DIR/frontend/v3"
rsync -a --delete "$APP_DIR/frontend-vite/dist/" "$APP_DIR/frontend/v3/"
find "$APP_DIR/frontend/v3" -type d -exec chmod 0755 {} +
find "$APP_DIR/frontend/v3" -type f -exec chmod 0644 {} +

install -m 0644 "$APP_DIR/deploy/product/opscenter-backend.service" /etc/systemd/system/opscenter-backend.service
install -m 0644 "$APP_DIR/deploy/product/Caddyfile" /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now opscenter-backend caddy
systemctl restart opscenter-backend
systemctl reload caddy

for _ in $(seq 1 30); do
  curl -fsS http://127.0.0.1:9091/openapi.json >/dev/null && break
  sleep 2
done
curl -fsS http://127.0.0.1:9091/openapi.json >/dev/null
curl -fsS http://127.0.0.1/ >/dev/null
echo "OpsCenter installation completed: http://$(hostname -I | awk '{print $1}')/"
