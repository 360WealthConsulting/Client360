#!/usr/bin/env bash
#
# Assert the connected database's schema is at the single migration head
# (0.9.13 Phase 3).
#
# READ-ONLY: it only runs `alembic current` and `alembic heads`. It never
# upgrades, downgrades, or drops anything, so it is safe against any database —
# unlike the reversibility check, which is guarded to disposable databases.
#
# Used as the final assertion of check_migrations_reversible.sh and independently
# to confirm the schema the test suite is about to use is fully migrated (a
# partially-applied schema is a silent source of confusing test failures).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Resolve a portable Python 3 interpreter into $PYTHON (see scripts/lib/pyenv.sh).
source "${REPO_ROOT}/scripts/lib/pyenv.sh"

head="$("$PYTHON" -m alembic heads 2>/dev/null | grep -oE '^[a-z0-9]+' | head -1)"
current="$("$PYTHON" -m alembic current 2>/dev/null | grep -oE '^[a-z0-9]+' | head -1)"

if [ -z "$head" ]; then
  echo "FAIL: could not determine the Alembic head." >&2
  exit 1
fi

if [ "$current" = "$head" ]; then
  echo "OK: schema is at head ($head)."
  exit 0
fi

echo "FAIL: schema is at '${current:-<none>}', expected head '$head'." >&2
echo "Run 'alembic upgrade head' against this database." >&2
exit 1
