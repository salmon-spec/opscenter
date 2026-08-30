#!/bin/sh
set -eu

cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required" >&2
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  compose() { docker compose "$@"; }
elif command -v docker-compose >/dev/null 2>&1; then
  compose() { docker-compose "$@"; }
else
  echo "Docker Compose v2 is required" >&2
  exit 1
fi

if [ ! -f .env ]; then
  echo "Create deploy/observability/.env from .env.example first" >&2
  exit 1
fi

set -a
. ./.env
set +a

case "${OPSCENTER_SERVER_ID:-}" in
  00000000-0000-0000-0000-000000000000|"")
    echo "Set OPSCENTER_SERVER_ID to the local host UUID" >&2
    exit 1
    ;;
esac

if ! printf '%s' "$OPSCENTER_SERVER_ID" | grep -Eq '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'; then
  echo "OPSCENTER_SERVER_ID is not a valid UUID" >&2
  exit 1
fi

LOKI_DATA_DIR=${LOKI_DATA_DIR:-/opt/opscenter-data/loki}
case "$LOKI_DATA_DIR" in
  /opt/opscenter-data/loki|/data/opscenter/loki) ;;
  *) echo "LOKI_DATA_DIR must use an approved dedicated path" >&2; exit 1 ;;
esac
mkdir -p "$LOKI_DATA_DIR"
chown 10001:10001 "$LOKI_DATA_DIR"
chmod 0755 "$LOKI_DATA_DIR"
export LOKI_DATA_DIR

compose config >/dev/null
compose pull
compose up -d

ready=0
attempt=1
while [ "$attempt" -le 30 ]; do
  if docker exec opscenter-loki wget -q --spider http://127.0.0.1:3100/ready >/dev/null 2>&1; then
    ready=1
    break
  fi
  attempt=$((attempt + 1))
  sleep 2
done

if [ "$ready" -ne 1 ]; then
  compose ps
  compose logs --tail=80 loki alloy
  echo "Loki did not become ready" >&2
  exit 1
fi

compose ps
echo "Observability stack is ready on ${LOKI_BIND_IP:-10.66.66.5}:3100"
