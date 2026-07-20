#!/usr/bin/env bash
#
# Post-deploy smoke test for a RUNNING Client360 instance.
#
#   usage: scripts/smoke.sh <base-url>
#   e.g.   scripts/smoke.sh https://client360.example
#          scripts/smoke.sh http://127.0.0.1:8000
#
# Verifies the four things that prove a deployment is actually up and safe:
#   1. liveness      — /health returns 200
#   2. readiness     — /readiness returns 200 "ready" with DB ok and migrations in sync
#   3. static assets — a stylesheet is served (the app is serving its own static files)
#   4. auth gate     — a protected route is NOT served anonymously
#
# Read-only and safe to run against production. Exits non-zero on any failure so it can gate
# a deploy pipeline (deploy -> smoke -> rollback-on-failure).
set -euo pipefail

BASE="${1:?usage: smoke.sh <base-url>   e.g. smoke.sh https://client360.example}"
BASE="${BASE%/}"

fail=0
pass() { printf '  [ OK ] %s\n' "$1"; }
bad()  { printf '  [FAIL] %s\n' "$1"; fail=1; }
code_of() { curl -s -o /dev/null -w '%{http_code}' "$@" 2>/dev/null || echo 000; }

echo "== Client360 smoke: ${BASE} =="

# 1. Liveness.
c=$(code_of "${BASE}/health")
[ "$c" = 200 ] && pass "/health 200" || bad "/health returned ${c}"

# 2. Readiness: 200 + status ready + database ok + migrations in sync.
rbody=$(curl -s "${BASE}/readiness" 2>/dev/null || echo "")
rcode=$(code_of "${BASE}/readiness")
if [ "$rcode" = 200 ] \
   && printf '%s' "$rbody" | grep -q '"status":"ready"' \
   && printf '%s' "$rbody" | grep -q '"database":"ok"' \
   && printf '%s' "$rbody" | grep -q '"in_sync":true'; then
  pass "/readiness ready (database ok, migrations in sync)"
else
  bad "/readiness not ready (http ${rcode}): ${rbody:0:200}"
fi

# 3. Static assets served.
c=$(code_of "${BASE}/static/css/workspace.css")
[ "$c" = 200 ] && pass "static asset served" || bad "static asset returned ${c}"

# 4. Auth gate: a protected route must not serve anonymously.
c=$(code_of -H 'Accept: text/html' "${BASE}/people")
case "$c" in
  200)               bad "/people served anonymously — auth gate NOT enforced" ;;
  30[0-9]|401|403)   pass "/people gated for anonymous (${c})" ;;
  *)                 bad "/people unexpected status ${c}" ;;
esac

echo
if [ "$fail" -ne 0 ]; then
  echo "SMOKE FAILED for ${BASE}" >&2
  exit 1
fi
echo "SMOKE PASSED for ${BASE}"
