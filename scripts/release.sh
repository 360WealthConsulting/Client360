#!/usr/bin/env bash
#
# Guarded release cut (Release 0.9.13, Phase 4).
#
#   scripts/release.sh <version> [--dry-run]
#   e.g. scripts/release.sh 0.9.13 --dry-run
#
# Runs every precondition that a release should satisfy and refuses if any fails.
# --dry-run reports pass/fail for all of them and stops WITHOUT tagging or
# pushing; without it, the same checks run and then an annotated tag is created
# (still not pushed — pushing stays a deliberate manual step).
#
# The 0.9.11 release merged with CI structurally broken and the changelog drifted
# for two releases. These guards exist so a release cannot be cut from a repo in
# that state.
#
# This script NEVER touches application code, migrations, or a database. It only
# reads git/CHANGELOG state and (outside --dry-run) creates a local tag.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VERSION="${1:?usage: release.sh <version> [--dry-run]   e.g. release.sh 0.9.13 --dry-run}"
DRY_RUN=0
[ "${2:-}" = "--dry-run" ] && DRY_RUN=1
TAG="v${VERSION}"

fail=0
pass() { printf '  [ OK ] %s\n' "$1"; }
bad()  { printf '  [FAIL] %s\n' "$1"; fail=1; }

echo "== release preflight for ${TAG} (dry-run=${DRY_RUN}) =="

# 1. Version is well-formed.
if [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  pass "version '$VERSION' is well-formed"
else
  bad "version '$VERSION' is not X.Y.Z"
fi

# 2. Working tree is clean.
if [ -z "$(git status --porcelain)" ]; then
  pass "working tree is clean"
else
  bad "working tree has uncommitted changes (commit or stash first)"
fi

# 3. The tag does not already exist (locally or on the remote).
if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
  bad "tag ${TAG} already exists locally"
elif git ls-remote --exit-code --tags origin "${TAG}" >/dev/null 2>&1; then
  bad "tag ${TAG} already exists on origin"
else
  pass "tag ${TAG} does not exist yet"
fi

# 4. Exactly one Alembic head (broken migration graph must not ship).
if scripts/check_migration_heads.sh >/dev/null 2>&1; then
  pass "exactly one Alembic head"
else
  bad "migration graph has multiple heads (run scripts/check_migration_heads.sh)"
fi

# 5. CHANGELOG has a dated entry for this version.
if grep -qE "^## \[${VERSION//./\\.}\][^#]*—\s*[0-9]{4}-[0-9]{2}-[0-9]{2}" CHANGELOG.md; then
  pass "CHANGELOG has a dated [${VERSION}] entry"
else
  bad "CHANGELOG has no dated [${VERSION}] entry (add one before releasing)"
fi

# 6. CHANGELOG passes its structural lint.
if python scripts/check_changelog.py >/dev/null 2>&1; then
  pass "CHANGELOG structural lint passes"
else
  bad "CHANGELOG structural lint fails (run scripts/check_changelog.py)"
fi

# 7. CI is green on the current commit (best-effort; needs gh + a pushed commit).
sha="$(git rev-parse HEAD)"
if command -v gh >/dev/null 2>&1; then
  conclusion="$(gh run list --commit "$sha" --limit 1 --json conclusion --jq '.[0].conclusion' 2>/dev/null || echo "")"
  case "$conclusion" in
    success) pass "CI is green on ${sha:0:7}" ;;
    "")      bad  "no CI run found for ${sha:0:7} (push and let CI complete first)" ;;
    *)       bad  "CI conclusion for ${sha:0:7} is '${conclusion}', not success" ;;
  esac
else
  bad "gh not available — cannot confirm CI is green"
fi

echo
if [ "$fail" -ne 0 ]; then
  echo "REFUSED: ${TAG} is not releasable — fix the [FAIL] items above." >&2
  exit 1
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY RUN: all preconditions pass. ${TAG} is releasable. No tag created."
  exit 0
fi

echo "All preconditions pass. Creating annotated tag ${TAG}..."
git tag -a "${TAG}" -m "Client360 ${TAG}"
echo "Created ${TAG} locally. Push it deliberately with:  git push origin ${TAG}"
