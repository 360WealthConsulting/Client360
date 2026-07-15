#!/usr/bin/env bash
#
# Developer Demo Mode control script (DEMO-ONLY).
#
#   scripts/demo.sh <command>
#
#   setup    create the demo DB (if missing), migrate to head, seed
#   reset    DROP + recreate the demo DB, migrate, reseed (idempotent)
#   start    start the demo server in the background
#   stop     stop the demo server
#   verify   confirm the target is a safe *_demo database
#   smoke    run demo smoke tests (safety, logins, visibility, routes)
#   status   show whether the demo server is running
#
# Safety: every command that touches the database refuses unless the database
# name ends in `_demo` and the environment is not production. This script never
# touches the normal Client360 database.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export CLIENT360_ENVIRONMENT="${CLIENT360_ENVIRONMENT:-development}"
export DEMO_DB_NAME="${DEMO_DB_NAME:-client360_demo}"
export DATABASE_URL="${DEMO_DATABASE_URL:-postgresql://localhost/${DEMO_DB_NAME}}"
export DEMO_PORT="${DEMO_PORT:-8360}"
export DEMO_HOST="${DEMO_HOST:-127.0.0.1}"
# Local demo secrets (fictional; not production values).
export SESSION_SECRET="${SESSION_SECRET:-demo-session-secret-not-for-production}"
export MICROSOFT_TOKEN_KEY="${MICROSOFT_TOKEN_KEY:-$(python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')}"

PIDFILE="${REPO_ROOT}/.demo-server.pid"
LOGFILE="${REPO_ROOT}/.demo-server.log"

# Production safety: this script must never run in production.
if [ "${CLIENT360_ENVIRONMENT}" = "production" ]; then
  echo "REFUSED: demo tooling must not run with CLIENT360_ENVIRONMENT=production." >&2
  exit 2
fi

_python_guard() { python -c "from app.demo.safety import assert_demo_database; print(assert_demo_database())"; }

case "${1:-}" in
  verify)
    echo "Environment : ${CLIENT360_ENVIRONMENT}"
    echo "Database URL: ${DATABASE_URL}"
    name="$(_python_guard)"
    echo "SAFE: target database '${name}' ends in '_demo' and environment is not production."
    ;;

  setup)
    _python_guard >/dev/null
    if ! psql -lqt | cut -d '|' -f1 | grep -qw "${DEMO_DB_NAME}"; then
      echo "Creating database ${DEMO_DB_NAME}..."; createdb "${DEMO_DB_NAME}"
    fi
    echo "Applying migrations to head..."; alembic upgrade head
    echo "Seeding demo data..."; python -m app.demo.seed
    echo "Setup complete. Start with: scripts/demo.sh start"
    ;;

  reset)
    _python_guard >/dev/null
    echo "Dropping and recreating ${DEMO_DB_NAME}..."
    psql -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${DEMO_DB_NAME}';" >/dev/null 2>&1 || true
    dropdb --if-exists "${DEMO_DB_NAME}"; createdb "${DEMO_DB_NAME}"
    echo "Applying migrations to head..."; alembic upgrade head
    echo "Reseeding demo data..."; python -m app.demo.seed
    echo "Reset complete."
    ;;

  start)
    _python_guard >/dev/null
    if [ -f "${PIDFILE}" ] && kill -0 "$(cat "${PIDFILE}")" 2>/dev/null; then
      echo "Demo server already running (pid $(cat "${PIDFILE}"))."; exit 0
    fi
    echo "Starting demo server on http://${DEMO_HOST}:${DEMO_PORT} ..."
    nohup uvicorn app.demo.demo_app:app --host "${DEMO_HOST}" --port "${DEMO_PORT}" >"${LOGFILE}" 2>&1 &
    echo $! > "${PIDFILE}"
    sleep 2
    if kill -0 "$(cat "${PIDFILE}")" 2>/dev/null; then
      echo "Demo server running (pid $(cat "${PIDFILE}"))."
      echo "  Login:  http://${DEMO_HOST}:${DEMO_PORT}/demo/login"
      echo "  Logs:   ${LOGFILE}"
    else
      echo "Demo server failed to start; see ${LOGFILE}" >&2; exit 1
    fi
    ;;

  stop)
    if [ -f "${PIDFILE}" ]; then
      pid="$(cat "${PIDFILE}")"
      if kill -0 "${pid}" 2>/dev/null; then kill "${pid}"; echo "Stopped demo server (pid ${pid})."; fi
      rm -f "${PIDFILE}"
    else
      echo "No demo server pidfile found."
    fi
    ;;

  status)
    if [ -f "${PIDFILE}" ] && kill -0 "$(cat "${PIDFILE}")" 2>/dev/null; then
      echo "Demo server RUNNING (pid $(cat "${PIDFILE}")) at http://${DEMO_HOST}:${DEMO_PORT}"
    else
      echo "Demo server not running."
    fi
    ;;

  smoke)
    _python_guard >/dev/null
    echo "== python smoke (safety, logins, role visibility) =="
    python -m app.demo.smoke
    if [ -f "${PIDFILE}" ] && kill -0 "$(cat "${PIDFILE}")" 2>/dev/null; then
      echo "== HTTP smoke (server routes) =="
      DEMO_BASE="http://${DEMO_HOST}:${DEMO_PORT}" python - <<'PYHTTP'
import os, urllib.request, urllib.parse, http.cookiejar
BASE=os.environ["DEMO_BASE"]
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a): return None
def code(op,p):
    try: return op.open(BASE+p, timeout=10).status
    except urllib.error.HTTPError as e: return e.code
    except Exception as e: return f"ERR {e}"
plain=urllib.request.build_opener(NoRedirect)
for p in ("/health","/readiness","/demo/login"):
    print(f"  GET {p} -> {code(plain,p)}")
def login(u,pw):
    cj=http.cookiejar.CookieJar(); op=urllib.request.build_opener(NoRedirect, urllib.request.HTTPCookieProcessor(cj))
    try: op.open(BASE+"/demo/login", data=urllib.parse.urlencode({"username":u,"password":pw}).encode(), timeout=10)
    except urllib.error.HTTPError: pass
    return op
admin=login("admin","demo-admin-pass")
for p in ("/","/work","/tax/intake","/tax/returns","/people","/portfolio","/admin/audit"):
    print(f"  GET {p} (admin) -> {code(admin,p)}")
client=login("client","demo-client-pass")
print(f"  GET /portal/ (client) -> {code(client,'/portal/')}")
PYHTTP
    else
      echo "(server not running; skipping HTTP smoke — run 'scripts/demo.sh start' first)"
    fi
    ;;

  *)
    echo "usage: scripts/demo.sh {setup|reset|start|stop|status|verify|smoke}" >&2
    exit 1
    ;;
esac
