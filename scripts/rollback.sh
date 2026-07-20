#!/usr/bin/env bash
#
# Migration rollback helper — downgrade the database to a target Alembic revision.
#
#   usage: scripts/rollback.sh --to <revision> [--dry-run] [--yes]
#   e.g.   scripts/rollback.sh --to f55d1s2p3t4c --dry-run
#
# A Client360 rollback is two steps:
#   (a) redeploy the PREVIOUS application artifact, and
#   (b) downgrade the database to the revision that artifact expects.
# This script performs (b). Every Client360 migration is reversible (verified in CI by
# check_migrations_reversible.sh), so a downgrade to a prior release's head is safe.
#
# Safety: shows current vs. target and requires an explicit confirmation (or --yes) before
# downgrading. --dry-run only reports. Never runs against a database whose URL is unset.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TO=""; DRY=0; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --to)      TO="${2:?--to needs a revision}"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --yes)     YES=1; shift ;;
    *) echo "unknown argument: $1" >&2; echo "usage: rollback.sh --to <revision> [--dry-run] [--yes]" >&2; exit 2 ;;
  esac
done
[ -n "$TO" ] || { echo "usage: rollback.sh --to <revision> [--dry-run] [--yes]" >&2; exit 2; }
[ -n "${DATABASE_URL:-}" ] || { echo "REFUSED: DATABASE_URL is not set." >&2; exit 2; }

current="$(alembic current 2>/dev/null | head -1 || true)"
echo "== rollback: database migration downgrade =="
echo "  database : ${DATABASE_URL}"
echo "  current  : ${current:-<none>}"
echo "  target   : ${TO}"

if [ "$DRY" -eq 1 ]; then
  echo "  -- dry run: would run 'alembic downgrade ${TO}' (no change made) --"
  exit 0
fi

if [ "$YES" -ne 1 ]; then
  printf "Downgrade the database to '%s'? Schema created after it will be dropped. Type 'yes': " "$TO"
  read -r ans
  [ "$ans" = "yes" ] || { echo "aborted."; exit 1; }
fi

alembic downgrade "$TO"
scripts/check_migration_heads.sh
echo "rolled back to ${TO}. Redeploy the matching application artifact if not already done."
