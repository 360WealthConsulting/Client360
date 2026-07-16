#!/usr/bin/env bash
#
# Prove the migration graph is reversible and leave the schema at head
# (0.9.13 Phase 3).
#
# A migration with a broken or missing downgrade is invisible until someone
# needs to roll back a bad deploy — the worst possible moment to discover it.
# This exercises every downgrade by walking the whole graph to base and back:
#
#   upgrade head  ->  downgrade base  ->  upgrade head
#
# It ends at head, so a CI job can run it in place of a plain `alembic upgrade
# head` and hand the schema straight to the test suite.
#
# It tests STRUCTURAL reversibility — that every downgrade's DDL runs — on an
# empty schema. It deliberately does not test downgrade-with-data: several
# downgrades DELETE from append-only tables (exception_events, audit_events),
# which their immutability triggers correctly block. That is by design, not a
# migration defect, so the check starts from a clean schema.
#
# DESTRUCTIVE: it drops the schema and `downgrade base` drops every table. It
# must only run against a disposable database — the same guard the test suite
# uses enforces that.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Refuse to run this against the dev or a production database. assert_test_database
# raises (non-zero under set -e) unless the target name is disposable.
db="$(python -c "from app.safety import assert_test_database; print(assert_test_database())")"

# Start from a clean schema so the result is deterministic regardless of any
# data a prior run left behind (see the append-only note above). psql is given
# the full DATABASE_URL so it connects the same way locally (trust auth) and in
# CI (host/port/password in the URI).
echo "== reset schema (${db}) =="
psql -q "${DATABASE_URL:?DATABASE_URL must be set}" \
  -c "SET client_min_messages TO WARNING; DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

head="$(alembic heads 2>/dev/null | grep -oE '^[a-z0-9]+' | head -1)"
if [ -z "$head" ]; then
  echo "FAIL: could not determine the Alembic head." >&2
  exit 1
fi

echo "== upgrade head =="
alembic upgrade head >/dev/null

echo "== downgrade base (exercises every migration's downgrade) =="
if ! alembic downgrade base >/tmp/downgrade.log 2>&1; then
  echo "FAIL: a migration is not reversible. Downgrade output:" >&2
  tail -20 /tmp/downgrade.log >&2
  exit 1
fi

echo "== upgrade head again =="
alembic upgrade head >/dev/null

# Reuse the standalone read-only check as the final assertion (DRY).
"${REPO_ROOT}/scripts/check_schema_at_head.sh"

echo "OK: migrations are reversible; schema is at head ($head)."
