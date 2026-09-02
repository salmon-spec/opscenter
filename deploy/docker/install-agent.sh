#!/usr/bin/env bash
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "install-agent.sh must run as root" >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SECRETS_FILE=${OPSCENTER_SECRETS_FILE:-/etc/opscenter/secrets.env}
token=$(sed -n 's/^LOCAL_AGENT_TOKEN=//p' "$SECRETS_FILE" | tail -1)
[ ${#token} -ge 16 ] || { echo "LOCAL_AGENT_TOKEN is missing or too short" >&2; exit 1; }

install -d -m 0755 /opt/opsagent
install -m 0644 "$ROOT/agent/opsagent.py" "$ROOT/agent/scanner.py" /opt/opsagent/
printf 'OPSAGENT_TOKEN=%s\n' "$token" >/etc/opsagent.env
chmod 0600 /etc/opsagent.env
install -m 0644 "$ROOT/deploy/docker/opsagent.service" /etc/systemd/system/opsagent.service
systemctl daemon-reload
systemctl enable --now opsagent.service
systemctl restart opsagent.service
