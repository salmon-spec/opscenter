#!/bin/sh
# Forced SSH command on VM100. The key cannot open a shell or choose a command.
set -eu
exec /usr/bin/timeout 45 /usr/bin/sudo -n /usr/bin/docker exec -i \
    opscenter-test-module-pve-backup-1 python3 /opt/module/pve_backup/import_oss_manifest.py
