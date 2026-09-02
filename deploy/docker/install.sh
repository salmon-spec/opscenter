#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo ./deploy/docker/install.sh" >&2
  exit 1
fi

SOURCE_DIR=$(cd "$(dirname "$0")/../.." && pwd)
APP_DIR=${OPSCENTER_HOME:-/opt/opscenter}
SECRETS_FILE=${OPSCENTER_SECRETS_FILE:-/etc/opscenter/secrets.env}
COMPOSE_FILE="$APP_DIR/deploy/docker/compose.yml"
INSTALL_HOST_AGENT=${INSTALL_HOST_AGENT:-true}

for command in docker openssl rsync curl systemctl; do
  command -v "$command" >/dev/null || { echo "Missing prerequisite: $command" >&2; exit 1; }
done
docker compose version >/dev/null

install -d -m 0755 "$APP_DIR" /etc/opscenter \
  /opt/opscenter-data/postgres /opt/opscenter-data/loki /opt/opscenter-data/config
if [ "$SOURCE_DIR" != "$APP_DIR" ]; then
  rsync -a --delete \
    --exclude=.git --exclude=.venv --exclude=venv --exclude=node_modules \
    --exclude=__pycache__ --exclude=.pytest_cache --exclude=data \
    --exclude=backup --exclude='backup_*' \
    "$SOURCE_DIR/" "$APP_DIR/"
fi

local_host=${LOCAL_HOST:-$(hostname -I | awk '{print $1}')}
http_bind=${OPSCENTER_HTTP_BIND:-0.0.0.0}
http_port=${OPSCENTER_HTTP_PORT:-80}
api_bind=${OPSCENTER_API_BIND:-127.0.0.1}
api_port=${OPSCENTER_API_PORT:-9091}
loki_bind_ip=${LOKI_BIND_IP:-$local_host}
loki_port=${LOKI_PORT:-3100}
agent_token=""
if [ "$INSTALL_HOST_AGENT" = false ]; then
  agent_token=$(systemctl show opsagent.service -p ExecStart --value 2>/dev/null | sed -n 's/.*--token \([^ ;}]\+\).*/\1/p' | head -1)
  [ ${#agent_token} -ge 16 ] || { echo "Existing opsagent token could not be read" >&2; exit 1; }
else
  agent_token=$(openssl rand -hex 32)
fi
if [ ! -f "$SECRETS_FILE" ]; then
  db_password=$(openssl rand -hex 24)
  admin_password=$(openssl rand -base64 24 | tr -d '\n')
  cat >"$SECRETS_FILE" <<EOF
POSTGRES_DB=opscenter
POSTGRES_USER=opscenter
POSTGRES_PASSWORD=$db_password
POSTGRES_DATA_DIR=/opt/opscenter-data/postgres
OPSCENTER_CONFIG_DIR=/opt/opscenter-data/config
OPSCENTER_HTTP_BIND=$http_bind
OPSCENTER_HTTP_PORT=$http_port
OPSCENTER_API_BIND=$api_bind
OPSCENTER_API_PORT=$api_port
LOKI_BIND_IP=$loki_bind_ip
LOKI_PORT=$loki_port
LOKI_DATA_DIR=/opt/opscenter-data/loki
LOKI_PUBLIC_URL=http://$local_host:$loki_port
OPS_AUTH_ENABLED=false
OPS_JWT_SECRET=$(openssl rand -hex 32)
OPS_ADMIN_USER=admin
OPS_ADMIN_PASSWORD=$admin_password
CREDENTIAL_KEY=$(openssl rand -hex 32)
LOCAL_AGENT_TOKEN=$agent_token
LOCAL_HOST=$local_host
LOCAL_SERVER_NAME=OpsCenter
PREVIEW_MODE=false
EOF
  echo "Generated $SECRETS_FILE; record OPS_ADMIN_PASSWORD before leaving this host."
fi
ensure_env() {
  grep -q "^$1=" "$SECRETS_FILE" || printf '%s=%s\n' "$1" "$2" >>"$SECRETS_FILE"
}
ensure_env POSTGRES_DATA_DIR /opt/opscenter-data/postgres
ensure_env OPSCENTER_CONFIG_DIR /opt/opscenter-data/config
ensure_env OPSCENTER_HTTP_BIND "$http_bind"
ensure_env OPSCENTER_HTTP_PORT "$http_port"
ensure_env OPSCENTER_API_BIND "$api_bind"
ensure_env OPSCENTER_API_PORT "$api_port"
ensure_env LOKI_BIND_IP "$loki_bind_ip"
ensure_env LOKI_PORT "$loki_port"
ensure_env LOKI_DATA_DIR /opt/opscenter-data/loki
ensure_env LOKI_PUBLIC_URL "http://$local_host:$loki_port"
ensure_env LOCAL_AGENT_TOKEN "$agent_token"
chmod 0600 "$SECRETS_FILE"

for file in groups.json services.json; do
  [ -f "/opt/opscenter-data/config/$file" ] || install -m 0644 "$APP_DIR/frontend/$file" "/opt/opscenter-data/config/$file"
done

if [ "$INSTALL_HOST_AGENT" = true ]; then
  "$APP_DIR/deploy/docker/install-agent.sh"
fi

docker compose --env-file "$SECRETS_FILE" -f "$COMPOSE_FILE" up -d --build
curl --retry 30 --retry-delay 2 --retry-connrefused -fsS "http://127.0.0.1:$(sed -n 's/^OPSCENTER_API_PORT=//p' "$SECRETS_FILE" | tail -1)/openapi.json" >/dev/null
curl --retry 30 --retry-delay 2 --retry-connrefused -fsS "http://127.0.0.1:$(sed -n 's/^OPSCENTER_HTTP_PORT=//p' "$SECRETS_FILE" | tail -1)/" >/dev/null
for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:$(sed -n 's/^LOKI_PORT=//p' "$SECRETS_FILE" | tail -1)/ready" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS "http://127.0.0.1:$(sed -n 's/^LOKI_PORT=//p' "$SECRETS_FILE" | tail -1)/ready" >/dev/null

echo "OpsCenter Docker installation completed: http://$local_host:$(sed -n 's/^OPSCENTER_HTTP_PORT=//p' "$SECRETS_FILE" | tail -1)/"
echo "For full management of this host, save its SSH credential in Manage Hosts; Docker socket is intentionally not mounted."
