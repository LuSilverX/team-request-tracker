#!/bin/sh
# Restore into a disposable database; never drops or overwrites the live database.
set -eu
cd "$(dirname "$0")/.."
file=${1:?Usage: sh scripts/verify_restore.sh backups/file.dump}
test -s "$file"
check_db="restore_check_$(date +%s)_$$"
cleanup() { docker compose exec -T db sh -c 'dropdb -U "$POSTGRES_USER" --if-exists "$1"' sh "$check_db"; }
trap cleanup EXIT INT TERM
docker compose exec -T db sh -c 'createdb -U "$POSTGRES_USER" "$1"' sh "$check_db"
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$1" --exit-on-error --no-owner' sh "$check_db" < "$file"
docker compose run --rm --no-deps -T -e POSTGRES_DB="$check_db" web python manage.py verify_data
docker compose run --rm --no-deps -T -e POSTGRES_DB="$check_db" web python manage.py migrate --check
printf 'Restoration verified in disposable database %s.\n' "$check_db"
