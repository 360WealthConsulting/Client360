#!/usr/bin/env bash
# Backup/restore rehearsal for Client360 (Release 0.9.9 Phase 7 / WP7.4).
#
# Restores a database dump into a *scratch* database and verifies the release
# is recoverable: exactly one Alembic head reachable, sentinel row counts, and a
# green test suite. It never touches production.
#
#   usage: scripts/restore_rehearsal.sh <dump-file> [scratch-db-name]
#
# Requires MICROSOFT_TOKEN_KEY to be exported (the SAME key used when the dump
# was taken) so encrypted Microsoft token caches remain decryptable; otherwise
# those rows are unreadable and accounts must reconnect.
set -euo pipefail
DUMP="${1:?usage: restore_rehearsal.sh <dump-file> [scratch-db-name]}"
DB="${2:-client360_restore_rehearsal}"

echo "== (re)creating scratch DB: $DB =="
dropdb --if-exists "$DB"
createdb "$DB"

echo "== restoring dump: $DUMP =="
if pg_restore --no-owner --dbname "$DB" "$DUMP" 2>/dev/null; then :; else psql -q -d "$DB" -f "$DUMP"; fi

export DATABASE_URL="postgresql://localhost/$DB"

echo "== upgrading to current head =="
alembic upgrade head

echo "== Alembic heads (expect exactly one) =="
alembic heads

echo "== sentinel row counts (capture before the suite mutates data) =="
for t in people households documents portal_document_requests tax_engagement_returns; do
  printf "  %-28s %s\n" "$t" "$(psql -tA -d "$DB" -c "SELECT count(*) FROM $t")"
done

echo "== running the test suite against the restored database =="
python -m pytest -q

echo "== restore rehearsal PASSED for $DB =="
