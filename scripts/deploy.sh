#!/usr/bin/env bash
#
# Deploy orchestration for Client360 (RC-4).
#
#   usage: scripts/deploy.sh --url <base-url> [--start-cmd "<cmd>"] [--rollback-to <rev>] [--dry-run]
#   e.g.   scripts/deploy.sh --url https://client360.example --rollback-to f55d1s2p3t4c
#
# Runs the safe deploy sequence for an already-built artifact:
#   1. alembic upgrade head              apply migrations
#   2. <start-cmd>                        start / replace the app process (infra-specific hook)
#   3. scripts/smoke.sh <url>            verify the running instance
#   4. on smoke failure, if --rollback-to is given:
#        scripts/rollback.sh --to <rev> --yes   (then exit non-zero)
#
# The only infrastructure-specific step is starting/replacing the app process (step 2); supply it
# with --start-cmd, or start it out of band. --dry-run prints the plan and changes nothing.
# Requires DATABASE_URL for the migration step.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

URL=""; START_CMD=""; ROLLBACK_TO=""; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --url)         URL="${2:?--url needs a value}"; shift 2 ;;
    --start-cmd)   START_CMD="${2:?--start-cmd needs a value}"; shift 2 ;;
    --rollback-to) ROLLBACK_TO="${2:?--rollback-to needs a revision}"; shift 2 ;;
    --dry-run)     DRY=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$URL" ] || { echo "usage: deploy.sh --url <base-url> [--start-cmd ...] [--rollback-to <rev>] [--dry-run]" >&2; exit 2; }

echo "== Client360 deploy =="
echo "  url         : ${URL}"
echo "  start-cmd   : ${START_CMD:-<none: start/replace the app out of band>}"
echo "  rollback-to : ${ROLLBACK_TO:-<none>}"

if [ "$DRY" -eq 1 ]; then
  echo "  -- dry run (no changes) --"
  echo "  1. alembic upgrade head"
  echo "  2. ${START_CMD:-<operator starts/replaces the app process>}"
  echo "  3. scripts/smoke.sh ${URL}"
  if [ -n "$ROLLBACK_TO" ]; then
    echo "  4. on smoke failure: scripts/rollback.sh --to ${ROLLBACK_TO} --yes"
  else
    echo "  4. on smoke failure: (no --rollback-to set; manual rollback)"
  fi
  exit 0
fi

[ -n "${DATABASE_URL:-}" ] || { echo "REFUSED: DATABASE_URL is not set (needed for migrations)." >&2; exit 2; }

echo "-- 1. applying migrations --"
alembic upgrade head
scripts/check_migration_heads.sh

if [ -n "$START_CMD" ]; then
  echo "-- 2. starting / replacing the app --"
  eval "$START_CMD"
else
  echo "-- 2. start/replace the app process now (no --start-cmd given), then press enter --"
  read -r _
fi

echo "-- 3. smoke --"
if scripts/smoke.sh "$URL"; then
  echo "DEPLOY OK for ${URL}"
else
  echo "SMOKE FAILED after deploy" >&2
  if [ -n "$ROLLBACK_TO" ]; then
    echo "-- 4. rolling migrations back to ${ROLLBACK_TO} --"
    scripts/rollback.sh --to "$ROLLBACK_TO" --yes || true
    echo "Migrations rolled back. Redeploy the previous application artifact to complete the rollback." >&2
  fi
  exit 1
fi
