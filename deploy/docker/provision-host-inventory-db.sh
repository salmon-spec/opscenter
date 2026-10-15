#!/bin/sh
set -eu

root=/opt/opscenter-test
secret="$root/secrets/host_inventory_db_url"
container=opscenter-test-db-1

if [ -e "$secret" ]; then
    echo "Host inventory database secret already exists; leaving it unchanged."
    exit 0
fi

install -d -m 700 "$root/secrets"
password=$(openssl rand -hex 32)
printf "CREATE ROLE host_inventory LOGIN PASSWORD '%s';\nCREATE DATABASE host_inventory OWNER host_inventory;\n" "$password" \
    | docker exec -i "$container" psql -U opscenter -d postgres -v ON_ERROR_STOP=1
printf 'postgresql://host_inventory:%s@db:5432/host_inventory' "$password" > "$secret"
chmod 600 "$secret"
unset password
echo "Host inventory database and restricted login provisioned."
