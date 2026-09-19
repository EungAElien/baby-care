#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
db_container="${SUPABASE_DB_CONTAINER:-supabase_db_baby-care-b03-local}"
api_python="${API_PYTHON:-$repo_dir/apps/api/.venv/bin/python}"

if [[ "$api_python" == */* ]]; then
  resolved_api_python="$api_python"
else
  resolved_api_python="$(command -v "$api_python" || true)"
fi

if ! docker inspect "$db_container" >/dev/null 2>&1; then
  echo "Local Supabase database container is not running: $db_container" >&2
  exit 1
fi
if [[ -z "$resolved_api_python" || ! -x "$resolved_api_python" ]]; then
  echo "Python test environment is missing: $api_python" >&2
  echo "Install apps/api/requirements-dev.lock first." >&2
  exit 1
fi
api_python="$resolved_api_python"

restore_set_option() {
  docker exec "$db_container" psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "grant baby_app to postgres with set false" >/dev/null
}

trap restore_set_option EXIT
docker exec "$db_container" psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -c "grant baby_app to postgres with set true" >/dev/null

database_url="$(
  cd "$repo_dir"
  npx supabase status -o json 2>/dev/null \
    | "$api_python" -c 'import json, sys; print(json.load(sys.stdin)["DB_URL"])'
)"
cd "$repo_dir/apps/api"
BABY_CARE_TEST_DATABASE_URL="$database_url" \
  "$api_python" -m pytest tests/integration/test_postgres_authorization.py -q --no-cov
