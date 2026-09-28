#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
db_container="${SUPABASE_DB_CONTAINER:-supabase_db_baby-care-b03-local}"

if ! docker inspect "$db_container" >/dev/null 2>&1; then
  echo "Local Supabase database container is not running: $db_container" >&2
  exit 1
fi

restore_set_option() {
  docker exec "$db_container" psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "grant baby_app to postgres with set false" >/dev/null
}

trap restore_set_option EXIT
docker exec "$db_container" psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -c "grant baby_app to postgres with set true" >/dev/null
docker exec -i "$db_container" psql -U postgres -d postgres \
  -v ON_ERROR_STOP=1 -f - < "$repo_dir/scripts/sql/test-supabase-context.sql"
