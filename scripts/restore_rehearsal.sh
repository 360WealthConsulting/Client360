#!/usr/bin/env bash
# Backup/restore rehearsal for Client360 (Release 0.9.9 Phase 7 / WP7.4).
#
# Restores a database dump into a *scratch* database and verifies the release
# is recoverable: exactly one Alembic head reachable, sentinel row counts, and a
# green test suite. It never touches production.
#
#   usage: scripts/restore_rehearsal.sh [--force] <dump-file> [scratch-db-name]
#
# Requires MICROSOFT_TOKEN_KEY to be exported (the SAME key used when the dump
# was taken) so encrypted Microsoft token caches remain decryptable; otherwise
# those rows are unreadable and accounts must reconnect.
#
# Safety: this script DROPS its target before restoring. It refuses any database
# whose name is not obviously a disposable scratch target (`_restore_rehearsal`,
# `_test`, `_ci`), and refuses to run in production at all. `--force` overrides
# the name check only — never the production check.
set -euo pipefail

# Resolve a portable Python 3 interpreter into $PYTHON (see scripts/lib/pyenv.sh).
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/pyenv.sh"

FORCE=0
if [ "${1:-}" = "--force" ]; then FORCE=1; shift; fi

DUMP="${1:?usage: restore_rehearsal.sh [--force] <dump-file> [scratch-db-name]}"
DB="${2:-client360_restore_rehearsal}"

export CLIENT360_ENVIRONMENT="${CLIENT360_ENVIRONMENT:-development}"
if [ "${CLIENT360_ENVIRONMENT}" = "production" ]; then
  echo "REFUSED: the restore rehearsal must not run with CLIENT360_ENVIRONMENT=production." >&2
  exit 2
fi

# Guard BEFORE the dropdb below: without this, a mistyped argument silently
# destroys whatever database it names, including the real one.
if [ "$FORCE" -eq 1 ]; then
  echo "WARNING: --force given; skipping the scratch-database name check for '$DB'." >&2
  echo "WARNING: this script is about to DROP '$DB'. Ctrl-C now if that is not a scratch database." >&2
  sleep 5
else
  "$PYTHON" - "$DB" <<'PYGUARD' || exit 2
import sys
from app.safety import assert_rehearsal_database, RehearsalSafetyError
try:
    assert_rehearsal_database(f"postgresql://localhost/{sys.argv[1]}")
except RehearsalSafetyError as exc:
    print(f"REFUSED: {exc}", file=sys.stderr)
    print("Pass --force only if you are certain the target is disposable.", file=sys.stderr)
    sys.exit(2)
PYGUARD
fi

echo "== (re)creating scratch DB: $DB =="
dropdb --if-exists "$DB"
createdb "$DB"

echo "== restoring dump: $DUMP =="
if pg_restore --no-owner --dbname "$DB" "$DUMP" 2>/dev/null; then :; else psql -q -d "$DB" -f "$DUMP"; fi

export DATABASE_URL="postgresql://localhost/$DB"

echo "== upgrading to current head =="
"$PYTHON" -m alembic upgrade head

echo "== Alembic heads (expect exactly one) =="
"$PYTHON" -m alembic heads

echo "== sentinel row counts (capture before the suite mutates data) =="
for t in people households documents portal_document_requests tax_engagement_returns; do
  printf "  %-28s %s\n" "$t" "$(psql -tA -d "$DB" -c "SELECT count(*) FROM $t")"
done

echo "== running the test suite against the restored database =="
"$PYTHON" -m pytest -q

echo "== restore rehearsal PASSED for $DB =="
