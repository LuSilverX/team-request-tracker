#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p backups
umask 077
file="backups/tracker-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$file"
test -s "$file"
printf 'Backup saved: %s\n' "$file"
