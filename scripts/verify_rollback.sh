#!/bin/sh
# Exercise only the reversible guard migration, on a restored disposable database.
set -eu
cd "$(dirname "$0")/.."
file=${1:?Usage: sh scripts/verify_rollback.sh backups/file.dump}
test -s "$file"
check_db="rollback_check_$(date +%s)_$$"
cleanup() { docker compose exec -T db sh -c 'dropdb -U "$POSTGRES_USER" --if-exists "$1"' sh "$check_db"; }
trap cleanup EXIT INT TERM
docker compose exec -T db sh -c 'createdb -U "$POSTGRES_USER" "$1"' sh "$check_db"
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$1" --exit-on-error --no-owner' sh "$check_db" < "$file"
docker compose run --rm --no-deps -T -e POSTGRES_DB="$check_db" web python manage.py migrate tracker 0001 --noinput
docker compose run --rm --no-deps -T -e POSTGRES_DB="$check_db" web python manage.py migrate --noinput
docker compose run --rm --no-deps -T -e POSTGRES_DB="$check_db" web python manage.py verify_data
printf 'Guard migration rollback and reapply verified in %s.\n' "$check_db"
