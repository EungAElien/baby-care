#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
db_container="${SUPABASE_DB_CONTAINER:-supabase_db_baby-care-b03-local}"
configured_python="${API_PYTHON:-$repo_dir/apps/api/.venv/bin/python}"

if ! docker inspect "$db_container" >/dev/null 2>&1; then
  echo "Local Supabase is not running. Run: npm run supabase:start" >&2
  exit 1
fi

if [[ "$configured_python" == */* ]]; then
  resolved_python="$configured_python"
else
  resolved_python="$(command -v "$configured_python" || true)"
fi
if [[ -z "${resolved_python:-}" || ! -x "$resolved_python" ]]; then
  echo "API Python was not found. Create apps/api/.venv or set API_PYTHON." >&2
  exit 1
fi

restore_set_option() {
  docker exec "$db_container" psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "grant baby_app to postgres with set false" >/dev/null
}
trap restore_set_option EXIT

docker exec "$db_container" psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -c "grant baby_app to postgres with set true" >/dev/null

status_json="$(cd "$repo_dir" && npx supabase status -o json 2>/dev/null)"
status_value() {
  STATUS_JSON="$status_json" node -e \
    'const value = JSON.parse(process.env.STATUS_JSON)[process.argv[1]]; if (!value) process.exit(2); process.stdout.write(value)' \
    "$1"
}

database_url="$(status_value DB_URL)"
api_url="$(status_value API_URL)"
anon_key="$(status_value ANON_KEY)"

echo "B-04 API: http://127.0.0.1:${BABY_CARE_PORT:-8080}"
echo "Supabase: $api_url"
echo "Mailpit: http://127.0.0.1:54324"

cd "$repo_dir/apps/api"
BABY_CARE_ENVIRONMENT=local \
BABY_CARE_HOST=127.0.0.1 \
BABY_CARE_PORT="${BABY_CARE_PORT:-8080}" \
BABY_CARE_DATABASE_URL="$database_url" \
BABY_CARE_SUPABASE_JWT_ISSUER="$api_url/auth/v1" \
BABY_CARE_SUPABASE_JWT_AUDIENCE=authenticated \
BABY_CARE_SUPABASE_JWKS_URL="$api_url/auth/v1/.well-known/jwks.json" \
BABY_CARE_SUPABASE_URL="$api_url" \
BABY_CARE_SUPABASE_PUBLISHABLE_KEY="$anon_key" \
BABY_CARE_REAUTHENTICATION_PROOF_SECRET=local-b04-proof-secret-not-for-production \
BABY_CARE_INVITE_BASE_URL=http://127.0.0.1:3000/invite \
BABY_CARE_CHILD_DATA_PRODUCTION_ENABLED=false \
PYTHONPATH=src \
  "$resolved_python" -m baby_care_api
