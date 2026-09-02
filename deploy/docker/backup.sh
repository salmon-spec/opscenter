#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
export OPSCENTER_HOME=${OPSCENTER_HOME:-/opt/opscenter}
export OPSCENTER_SECRETS_FILE=${OPSCENTER_SECRETS_FILE:-/etc/opscenter/secrets.env}
export OPSCENTER_MUTABLE_DIR=${OPSCENTER_MUTABLE_DIR:-/opt/opscenter-data/config}
export DB_CONTAINER=${DB_CONTAINER:-opscenter-db-1}
export LOKI_CONTAINER=${LOKI_CONTAINER:-opscenter-loki-1}
exec "$ROOT/deploy/product/backup.sh" "$@"
