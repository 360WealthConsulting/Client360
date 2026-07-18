#!/usr/bin/env bash
#
# dev.sh — the one documented local development workflow (E1.2).
#
#   scripts/dev.sh <command>
#
#   setup     install dependencies, create the dev DB if missing, migrate to head
#   doctor    validate the environment (Python, DB connectivity, schema, config)
#   migrate   apply Alembic migrations to head (non-destructive)
#   run       launch the application (uvicorn app.main:app)
#   status    show the resolved development configuration
#   db-up     start the Docker Compose PostgreSQL service (if you use Docker)
#   db-down   stop the Docker Compose PostgreSQL service
#   help      show this message
#
# Safety:
#   * Refuses to run with CLIENT360_ENVIRONMENT=production.
#   * NEVER drops or resets the development database (that is the test suite's job,
#     against a disposable *_test database — see scripts/test.sh). `setup` only
#     CREATES the dev DB if it is missing and applies forward migrations.
#
# This is the plain-development path. `scripts/demo.sh` (demo DB) and
# `scripts/test.sh` (disposable test DB) remain the paths for those purposes.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Resolve a portable Python 3 interpreter into $PYTHON.
# shellcheck source=scripts/lib/pyenv.sh
source "${REPO_ROOT}/scripts/lib/pyenv.sh"

export CLIENT360_ENVIRONMENT="${CLIENT360_ENVIRONMENT:-development}"
DEV_DB_NAME="${DEV_DB_NAME:-client360}"
export DATABASE_URL="${DATABASE_URL:-postgresql://localhost/${DEV_DB_NAME}}"
DEV_HOST="${DEV_HOST:-127.0.0.1}"
DEV_PORT="${DEV_PORT:-8000}"
COMPOSE_FILE="${REPO_ROOT}/infrastructure/docker-compose.yml"

if [ "${CLIENT360_ENVIRONMENT}" = "production" ]; then
  echo "REFUSED: dev tooling must not run with CLIENT360_ENVIRONMENT=production." >&2
  exit 2
fi

_db_exists() { psql -lqt 2>/dev/null | cut -d '|' -f1 | grep -qw "${DEV_DB_NAME}"; }

cmd_status() {
  echo "Client360 development configuration"
  echo "  Environment : ${CLIENT360_ENVIRONMENT}"
  echo "  Python      : $("$PYTHON" --version 2>&1)  (${PYTHON})"
  echo "  Database URL: ${DATABASE_URL}"
  echo "  App address : http://${DEV_HOST}:${DEV_PORT}"
  echo "  Compose file: ${COMPOSE_FILE}"
}

cmd_setup() {
  echo "Installing dependencies (pinned)..."
  "$PYTHON" -m pip install -r requirements.txt -r requirements-dev.txt
  if command -v createdb >/dev/null 2>&1; then
    if _db_exists; then
      echo "Database ${DEV_DB_NAME} already exists (left intact)."
    else
      echo "Creating database ${DEV_DB_NAME}..."; createdb "${DEV_DB_NAME}"
    fi
    echo "Applying migrations to head..."; "$PYTHON" -m alembic upgrade head
  else
    echo "NOTE: no local 'createdb' found. Start PostgreSQL via 'scripts/dev.sh db-up'"
    echo "      (Docker) or install PostgreSQL, then run 'scripts/dev.sh migrate'."
  fi
  echo "Setup complete. Next: scripts/dev.sh doctor && scripts/dev.sh run"
}

cmd_migrate() {
  echo "Applying migrations to head (non-destructive)..."
  "$PYTHON" -m alembic upgrade head
}

cmd_run() {
  echo "Starting Client360 on http://${DEV_HOST}:${DEV_PORT} (Ctrl-C to stop)..."
  exec "$PYTHON" -m uvicorn app.main:app --host "${DEV_HOST}" --port "${DEV_PORT}" --reload
}

cmd_doctor() {
  local ok=1
  echo "== Client360 environment doctor =="

  # 1. Python version.
  if "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info[:2]==(3,12) else 1)'; then
    echo "  [ok]   Python 3.12 ($("$PYTHON" --version 2>&1))"
  else
    echo "  [warn] Expected Python 3.12; found $("$PYTHON" --version 2>&1) (.python-version pins 3.12)"
  fi

  # 2. DATABASE_URL present.
  if [ -n "${DATABASE_URL:-}" ]; then
    echo "  [ok]   DATABASE_URL is set"
  else
    echo "  [FAIL] DATABASE_URL is not set (required)"; ok=0
  fi

  # 3. PostgreSQL connectivity.
  if command -v pg_isready >/dev/null 2>&1 && pg_isready >/dev/null 2>&1; then
    echo "  [ok]   PostgreSQL is accepting connections"
  else
    echo "  [warn] PostgreSQL not reachable via pg_isready (start it: scripts/dev.sh db-up)"
  fi

  # 4. Dev database exists.
  if _db_exists; then
    echo "  [ok]   Database ${DEV_DB_NAME} exists"
  else
    echo "  [warn] Database ${DEV_DB_NAME} not found (create it: scripts/dev.sh setup)"
  fi

  # 5. Schema at head.
  if "$PYTHON" - <<'PY' 2>/dev/null; then
import sys
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine
import os
cfg = Config("alembic.ini")
heads = set(ScriptDirectory.from_config(cfg).get_heads())
eng = create_engine(os.environ["DATABASE_URL"])
with eng.connect() as conn:
    current = set(MigrationContext.configure(conn).get_current_heads())
sys.exit(0 if current == heads else 1)
PY
    echo "  [ok]   Database schema is at Alembic head"
  else
    echo "  [warn] Database schema is not at head (run: scripts/dev.sh migrate)"
  fi

  # 6. Application configuration warnings (missing-variable detection).
  "$PYTHON" - <<'PY'
from app.config import configuration_warnings
warnings = configuration_warnings()
if not warnings:
    print("  [ok]   Application configuration: no warnings")
else:
    for w in warnings:
        print(f"  [warn] {w}")
PY

  [ "$ok" -eq 1 ] && echo "Doctor: no blocking problems." || { echo "Doctor: blocking problems found."; return 1; }
}

_compose() {
  if command -v docker >/dev/null 2>&1; then
    docker compose -f "${COMPOSE_FILE}" "$@"
  else
    echo "Docker is not installed. Install Docker Desktop, or use a local PostgreSQL." >&2
    return 2
  fi
}

cmd_db_up()   { echo "Starting PostgreSQL via Docker Compose..."; _compose up -d db; }
cmd_db_down() { echo "Stopping Docker Compose services..."; _compose down; }

cmd_help() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; }

case "${1:-help}" in
  setup)   cmd_setup ;;
  doctor)  cmd_doctor ;;
  migrate) cmd_migrate ;;
  run)     cmd_run ;;
  status)  cmd_status ;;
  db-up)   cmd_db_up ;;
  db-down) cmd_db_down ;;
  help|-h|--help) cmd_help ;;
  *) echo "Unknown command: ${1}" >&2; cmd_help; exit 2 ;;
esac
