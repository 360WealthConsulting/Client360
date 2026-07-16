#!/usr/bin/env bash
#
# Test database control script (TEST-ONLY).
#
#   scripts/test.sh <command>
#
#   setup    create the test DB (if missing) and migrate to head
#   reset    DROP + recreate the schema and migrate (pristine, ~1s)
#   run      reset, then run the full suite (the recommended entry point)
#   fast     run the suite WITHOUT resetting (quick iteration)
#   verify   confirm the target is a safe, disposable test database
#   status   show the test DB and how much data it holds
#
# Safety: every command that touches the database refuses unless the database
# name ends in `_test` (or another disposable suffix) and the environment is not
# production. This script never touches the normal Client360 database.
#
# Why `run` resets: leftover rows are what made this suite flaky and slow. A
# reset costs ~1s; a stale database costs an afternoon. See issue #24.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export CLIENT360_ENVIRONMENT="${CLIENT360_ENVIRONMENT:-development}"
export TEST_DB_NAME="${TEST_DB_NAME:-client360_test}"
export DATABASE_URL="${TEST_DATABASE_URL:-postgresql://localhost/${TEST_DB_NAME}}"
# Local test secrets (fictional; not production values).
export SESSION_SECRET="${SESSION_SECRET:-test-session-secret-not-for-production}"

# Production safety: this script must never run in production.
if [ "${CLIENT360_ENVIRONMENT}" = "production" ]; then
  echo "REFUSED: test tooling must not run with CLIENT360_ENVIRONMENT=production." >&2
  exit 2
fi

# The guard is the boundary: a raised SuiteSafetyError exits non-zero under `set -e`.
_python_guard() { python -c "from app.safety import assert_test_database; print(assert_test_database())"; }

_db_exists() { psql -lqt | cut -d '|' -f1 | grep -qw "${TEST_DB_NAME}"; }

# Quiet on success, fully verbose on failure: alembic logs every revision at INFO
# to stderr, which buries the one line that matters when a migration breaks.
_migrate() {
  echo "Applying migrations to head..."
  local out
  if ! out="$(alembic upgrade head 2>&1)"; then
    echo "$out" >&2
    return 1
  fi
}

case "${1:-}" in
  verify)
    echo "Environment : ${CLIENT360_ENVIRONMENT}"
    echo "Database URL: ${DATABASE_URL}"
    name="$(_python_guard)"
    echo "SAFE: target database '${name}' is disposable and the environment is not production."
    ;;

  setup)
    _python_guard >/dev/null
    if ! _db_exists; then
      echo "Creating database ${TEST_DB_NAME}..."; createdb "${TEST_DB_NAME}"
    fi
    _migrate
    echo "Setup complete. Run the suite with: scripts/test.sh run"
    ;;

  reset)
    _python_guard >/dev/null
    if ! _db_exists; then
      echo "Creating database ${TEST_DB_NAME}..."; createdb "${TEST_DB_NAME}"
    else
      # Drop the schema rather than the database: no need to terminate sessions,
      # and it leaves nothing behind — including the seeded reference data, which
      # the migrations put back.
      echo "Dropping and recreating schema in ${TEST_DB_NAME}..."
      psql -q -d "${TEST_DB_NAME}" \
        -c "SET client_min_messages TO WARNING; DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
    fi
    _migrate
    echo "Reset complete: ${TEST_DB_NAME} is pristine."
    ;;

  run)
    "$0" reset
    echo "== running the full suite against ${TEST_DB_NAME} =="
    shift || true
    python -m pytest -q "$@"
    ;;

  fast)
    _python_guard >/dev/null
    shift || true
    python -m pytest -q "$@"
    ;;

  status)
    if ! _db_exists; then
      echo "Test database ${TEST_DB_NAME} does not exist. Run: scripts/test.sh setup"; exit 0
    fi
    echo "Test database : ${TEST_DB_NAME}"
    echo "Alembic head  : $(psql -tA -d "${TEST_DB_NAME}" -c 'select version_num from alembic_version' 2>/dev/null || echo 'not migrated')"
    echo "Size          : $(psql -tA -d "${TEST_DB_NAME}" -c 'select pg_size_pretty(pg_database_size(current_database()))' 2>/dev/null)"
    echo "Leftover rows : $(psql -tA -d "${TEST_DB_NAME}" -c 'select count(*) from workflow_steps' 2>/dev/null || echo '-') workflow_steps"
    ;;

  *)
    echo "usage: scripts/test.sh {setup|reset|run|fast|verify|status}" >&2
    exit 1
    ;;
esac
