#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SECRETS_FILE=${OPSCENTER_SECRETS_FILE:-/etc/opscenter/secrets.env}
COMPOSE_FILE="$ROOT/deploy/docker/compose.yml"

docker compose --env-file "$SECRETS_FILE" -f "$COMPOSE_FILE" config -q
docker compose --env-file "$SECRETS_FILE" -f "$COMPOSE_FILE" ps --status running
curl -fsS "http://127.0.0.1:$(sed -n 's/^OPSCENTER_API_PORT=//p' "$SECRETS_FILE" | tail -1)/openapi.json" | grep -q '4.8.2'
curl -fsS "http://127.0.0.1:$(sed -n 's/^OPSCENTER_HTTP_PORT=//p' "$SECRETS_FILE" | tail -1)/" >/dev/null
curl -fsS "http://127.0.0.1:$(sed -n 's/^LOKI_PORT=//p' "$SECRETS_FILE" | tail -1)/ready" >/dev/null
echo "OpsCenter Docker verification passed"
