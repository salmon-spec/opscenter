#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script as root so the database secret remains protected."
    exit 1
fi

root=/opt/opscenter-test
secret="$root/secrets/service_catalog_db_url"
container=opscenter-test-db-1

if [ -e "$secret" ]; then
    echo "Service catalog database secret already exists; leaving it unchanged."
    exit 0
fi

install -d -m 700 "$root/secrets"
password=$(openssl rand -hex 32)
if ! docker exec "$container" psql -U opscenter -d postgres -Atqc "SELECT 1 FROM pg_roles WHERE rolname='service_catalog'" | grep -q 1; then
    printf "CREATE ROLE service_catalog LOGIN PASSWORD '%s';\n" "$password" \
        | docker exec -i "$container" psql -U opscenter -d postgres -v ON_ERROR_STOP=1
else
    printf "ALTER ROLE service_catalog WITH PASSWORD '%s';\n" "$password" \
        | docker exec -i "$container" psql -U opscenter -d postgres -v ON_ERROR_STOP=1
fi
if ! docker exec "$container" psql -U opscenter -d postgres -Atqc "SELECT 1 FROM pg_database WHERE datname='service_catalog'" | grep -q 1; then
    docker exec "$container" createdb -U opscenter -O service_catalog service_catalog
fi
printf 'postgresql://service_catalog:%s@db:5432/service_catalog' "$password" > "$secret"
chmod 600 "$secret"
unset password
echo "Service catalog database and restricted login provisioned."
