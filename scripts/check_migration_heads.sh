#!/usr/bin/env bash
#
# Assert the migration graph has exactly one head (0.9.13 Phase 3).
#
# Two branches that each add a migration produce two Alembic heads. `alembic
# upgrade head` then fails ("Multiple head revisions are present"), and the
# failure only surfaces at deploy or on the next migration — long after the
# merge that caused it. This check moves that failure into CI, on the PR.
#
# It shells out to `alembic heads` rather than grepping migration files: some
# revisions pack `revision=...; down_revision=...` onto one line, which defeats
# line-anchored parsing.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

heads="$(alembic heads 2>/dev/null | grep -c '(head)' || true)"

if [ "$heads" -eq 1 ]; then
  echo "OK: exactly one Alembic head."
  alembic heads 2>/dev/null | sed 's/^/  /'
  exit 0
fi

echo "FAIL: expected exactly one Alembic head, found ${heads}." >&2
alembic heads 2>/dev/null | sed 's/^/  /' >&2
echo "Two branches each added a migration. Merge the heads with:" >&2
echo "  alembic merge -m 'merge heads' <rev1> <rev2>" >&2
exit 1
