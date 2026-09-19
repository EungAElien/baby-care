#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
db_container="${SUPABASE_DB_CONTAINER:-supabase_db_baby-care-b03-local}"
supabase_workdir="${SUPABASE_WORKDIR:-$repo_dir}"
configured_python="${API_PYTHON:-$repo_dir/apps/api/.venv/bin/python}"
artifact_dir="${BABY_CARE_VERIFICATION_ARTIFACT_DIR:-}"

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

status_json="$(cd "$repo_dir" && npx supabase --workdir "$supabase_workdir" status -o json 2>/dev/null)"
status_value() {
  STATUS_JSON="$status_json" node -e \
    'const value = JSON.parse(process.env.STATUS_JSON)[process.argv[1]]; if (!value) process.exit(2); process.stdout.write(value)' \
    "$1"
}

database_url="$(status_value DB_URL)"
api_url="$(status_value API_URL)"
anon_key="$(status_value ANON_KEY)"
service_role_key="$(status_value SERVICE_ROLE_KEY)"
issuer="$api_url/auth/v1"
mailpit_url="$(status_value MAILPIT_URL)"

pytest_args=(-q)
if [[ -n "$artifact_dir" ]]; then
  mkdir -p "$artifact_dir"
  pytest_args+=(
    "--junitxml=$artifact_dir/api-integration-junit.xml"
    "--cov-report=xml:$artifact_dir/api-coverage.xml"
  )
fi

if [[ "$configured_python" == */* ]]; then
  resolved_python="$configured_python"
else
  resolved_python="$(command -v "$configured_python" || true)"
fi

if [[ -n "${resolved_python:-}" && -x "$resolved_python" ]]; then
  cd "$repo_dir/apps/api"
  BABY_CARE_TEST_DATABASE_URL="$database_url" \
  BABY_CARE_TEST_SUPABASE_URL="$api_url" \
  BABY_CARE_TEST_SUPABASE_ANON_KEY="$anon_key" \
  BABY_CARE_TEST_SUPABASE_SERVICE_ROLE_KEY="$service_role_key" \
  BABY_CARE_TEST_SUPABASE_ISSUER="$issuer" \
  BABY_CARE_TEST_SUPABASE_JWKS_URL="$api_url/auth/v1/.well-known/jwks.json" \
  BABY_CARE_TEST_MAILPIT_URL="$mailpit_url" \
  BABY_CARE_REQUIRE_INTEGRATION=1 \
    "$resolved_python" -m pytest "${pytest_args[@]}"
  exit 0
fi

docker_api_url="${api_url/127.0.0.1/host.docker.internal}"
docker_database_url="${database_url/127.0.0.1/host.docker.internal}"
docker_mailpit_url="${mailpit_url/127.0.0.1/host.docker.internal}"
docker build -q -t baby-care-api:b04-integration "$repo_dir/apps/api" >/dev/null
docker run --rm --add-host=host.docker.internal:host-gateway \
  --user 0 \
  --entrypoint sh \
  -v "$repo_dir:/repo" \
  -w /repo/apps/api \
  -e "BABY_CARE_TEST_DATABASE_URL=$docker_database_url" \
  -e "BABY_CARE_TEST_SUPABASE_URL=$docker_api_url" \
  -e "BABY_CARE_TEST_SUPABASE_ANON_KEY=$anon_key" \
  -e "BABY_CARE_TEST_SUPABASE_SERVICE_ROLE_KEY=$service_role_key" \
  -e "BABY_CARE_TEST_SUPABASE_ISSUER=$issuer" \
  -e "BABY_CARE_TEST_SUPABASE_JWKS_URL=$docker_api_url/auth/v1/.well-known/jwks.json" \
  -e "BABY_CARE_TEST_MAILPIT_URL=$docker_mailpit_url" \
  -e "BABY_CARE_REQUIRE_INTEGRATION=1" \
  baby-care-api:b04-integration \
  -c 'python -m pip install --quiet --require-hashes --no-deps -r requirements-dev.lock && PYTHONPATH=src python -m pytest -q'
