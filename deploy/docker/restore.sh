#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SECRETS_FILE=${OPSCENTER_SECRETS_FILE:-/etc/opscenter/secrets.env}
COMPOSE_FILE="$ROOT/deploy/docker/compose.yml"
export OPSCENTER_MUTABLE_DIR=${OPSCENTER_MUTABLE_DIR:-/opt/opscenter-data/config}

docker compose --env-file "$SECRETS_FILE" -f "$COMPOSE_FILE" stop backend web loki
COMPOSE_FILE="$COMPOSE_FILE" SERVICE_CONTROL=false \
  "$ROOT/deploy/product/restore.sh" "$@"
"$ROOT/deploy/docker/install.sh"
echo "Docker restore completed"
