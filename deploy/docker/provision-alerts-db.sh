#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script as root so the database secret remains protected."
    exit 1
fi

root=/opt/opscenter-test
secret="$root/secrets/alerts_db_url"
container=opscenter-test-db-1

if [ -s "$secret" ]; then
    echo "Alerts database secret already exists; leaving it unchanged."
    exit 0
fi

install -d -m 700 "$root/secrets"
if docker exec "$container" psql -U opscenter -d postgres -Atqc "SELECT 1 FROM pg_roles WHERE rolname='alerts'" | grep -q 1 \
    || docker exec "$container" psql -U opscenter -d postgres -Atqc "SELECT 1 FROM pg_database WHERE datname='alerts'" | grep -q 1; then
    echo "Alerts role or database already exists without a non-empty secret; refusing to change it." >&2
    exit 1
fi

password=$(openssl rand -hex 32)
printf "CREATE ROLE alerts LOGIN PASSWORD '%s';\n" "$password" \
    | docker exec -i "$container" psql -U opscenter -d postgres -v ON_ERROR_STOP=1
docker exec "$container" createdb -U opscenter -O alerts alerts
printf 'postgresql://alerts:%s@db:5432/alerts' "$password" > "$secret"
chmod 600 "$secret"
unset password
echo "Alerts database and restricted login provisioned."
