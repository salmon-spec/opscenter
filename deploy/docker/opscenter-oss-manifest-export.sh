#!/bin/sh
# PVE-side metadata-only exporter. OSS credentials remain on the PVE host.
set -eu
umask 077
listing=$(/usr/bin/mktemp /run/opscenter-oss-manifest.XXXXXX)
trap '/usr/bin/rm -f "$listing"' EXIT HUP INT TERM
if ! /usr/bin/timeout 120 /usr/bin/ossutil ls oss://pve-backup-qiushui/pve/ > "$listing" 2>/dev/null; then
    /usr/bin/logger -t opscenter-oss-manifest 'OSS listing failed'
    exit 1
fi
if ! /usr/bin/grep -Eq '^Object Number is: [1-9][0-9]*$' "$listing"; then
    /usr/bin/logger -t opscenter-oss-manifest 'OSS listing incomplete'
    exit 1
fi
if ! result=$(/usr/bin/timeout 60 /usr/bin/ssh -F none \
    -i /root/.ssh/opscenter-oss-manifest -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/root/.ssh/known_hosts \
    -o BatchMode=yes -o ConnectTimeout=5 iplus@10.66.66.6 < "$listing"); then
    /usr/bin/logger -t opscenter-oss-manifest 'VM100 manifest import failed'
    exit 1
fi
case "$result" in
    'imported '*' OSS objects') /usr/bin/logger -t opscenter-oss-manifest "$result" ;;
    *) /usr/bin/logger -t opscenter-oss-manifest 'VM100 importer returned unexpected result'; exit 1 ;;
esac
