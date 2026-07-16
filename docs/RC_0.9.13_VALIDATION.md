# RC-0.9.13 — Release Candidate Validation (Release 0.9.13 · Platform Foundation)

**Scope:** Release 0.9.13 candidate validation.
**Baseline:** `v0.9.12`
**Candidate:** `c8dfe26` on branch `feature/test-isolation`
**CI:** **success**
**Validator:** <name>
**Date:** 2026-07-16

> Merge gate. Not merged; tag not yet applied.

---

## 0. Final recommendation

<!-- FILL after the gates below are green -->

## 1. Build, suite, and static gates

| Check | Method | Result |
|---|---|---|
| Full test suite | `scripts/test.sh run` | **PASS** (581 passed, 5 skipped) |
| Compile | `python -m compileall app tests migrations` | **PASS** |
| Whitespace | `git diff --check` | **PASS** |
| Ruff gate | `python scripts/ruff_gate.py` | **PASS** |
| CHANGELOG | `python scripts/check_changelog.py` | **PASS** |

## 2. Migration integrity

| Check | Method | Result |
|---|---|---|
| Exactly one head | `scripts/check_migration_heads.sh` | **PASS** |
| Reversible (down→base→head) | `scripts/check_migrations_reversible.sh` | PENDING |
| Schema at head | `scripts/check_schema_at_head.sh` | PENDING |
| Alembic head | — | `u1f9c0i9h8g7` |

## 3. Domain validation

<!-- FILL: per-release functional checks (routes, authorization, record scope,
     data). Add one subsection per domain touched. -->

## 4. Defects

<!-- FILL: numbered defects with disposition, or "None." -->

## 5. Verdict

<!-- FILL: SAFE TO MERGE / CONCERNS / BLOCKED -->
