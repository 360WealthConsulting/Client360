#!/usr/bin/env bash
#
# Data-SAFE migration rollback — downgrade the database to a target Alembic revision, but only AFTER a
# verified backup exists. Thin wrapper over app/deploy/rollback.py (the tested implementation).
#
#   usage: scripts/rollback.sh --to <revision> [--dry-run] [--yes] [--backup-dir <dir>]
#   e.g.   scripts/rollback.sh --to billing01 --dry-run
#          scripts/rollback.sh --to billing01            # prompts, backs up + verifies, then downgrades
#
# A Client360 rollback is two steps: (a) redeploy the PREVIOUS application artifact, and (b) downgrade the
# database to the revision that artifact expects. This script performs (b), DATA-SAFELY:
#
#   * Alembic downgrades are structurally reversible but NOT data-preserving (several downgrades DELETE
#     from append-only tables; every drop_table/drop_column destroys its data). So this refuses to
#     downgrade unless a pg_dump backup is taken AND verified (pg_restore --list) first.
#   * The backup location is printed BEFORE any downgrade begins; if backup or verification fails, no
#     downgrade runs (fail closed).
#   * --dry-run reports the plan + backup location and makes no change.
#   * Downgrade requires explicit confirmation (interactive 'yes', or --yes).
#
# Set CLIENT360_BACKUP_DIR to durable storage for the backup (defaults to <repo>/backups).
# Automatic restore-on-failure is intentionally NOT done — see docs/DATABASE.md for the manual restore.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Resolve a portable Python 3 interpreter into $PYTHON (see scripts/lib/pyenv.sh).
source "$REPO_ROOT/scripts/lib/pyenv.sh"

[ -n "${DATABASE_URL:-}" ] || { echo "REFUSED: DATABASE_URL is not set." >&2; exit 2; }

exec "$PYTHON" -m app.deploy.rollback "$@"
