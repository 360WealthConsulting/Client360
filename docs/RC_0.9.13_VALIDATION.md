# RC-0.9.13 — Release Candidate Validation (Release 0.9.13 · Platform Foundation)

**Scope:** Release 0.9.13 candidate validation.
**Baseline:** `v0.9.12`
**Candidate:** `3b8c942` on branch `feature/test-isolation`
**CI:** **success**
**Validator:** Claude (release automation)
**Date:** 2026-07-16

> Merge gate. Not merged; tag not yet applied.

---

## 0. Final recommendation

**SAFE TO MERGE** — all gates below are green; no defects; no product/business-logic or schema change.

## 1. Build, suite, and static gates

| Check | Method | Result |
|---|---|---|
| Full test suite | `scripts/test.sh run` | **PASS** (589 passed, 5 skipped) |
| Compile | `python -m compileall app tests migrations` | **PASS** |
| Whitespace | `git diff --check` | **PASS** |
| Ruff gate | `python scripts/ruff_gate.py` | **PASS** |
| CHANGELOG | `python scripts/check_changelog.py` | **PASS** |

## 2. Migration integrity

| Check | Method | Result |
|---|---|---|
| Exactly one head | `scripts/check_migration_heads.sh` | **PASS** |
| Reversible (down→base→head) | `scripts/check_migrations_reversible.sh` | **PASS** |
| Schema at head | `scripts/check_schema_at_head.sh` | **PASS** |
| Alembic head | — | `u1f9c0i9h8g7` |

## 3. Domain validation

0.9.13 is developer-platform, testing, and release tooling only — no product routes, authorization, or record-scope changed. Regression confirmed: demo starts from the branch, all 21 staff routes return 200 inside the shell, authorization/record-scope suites pass. Alembic head unchanged (`u1f9c0i9h8g7`); no migration added.

## 4. Defects

None.

## 5. Verdict

**SAFE TO MERGE.** Every acceptance gate from Phases 0–5 is green on candidate `3b8c942` (CI success). Follow-ups tracked in #26 (Ruff backlog) and the remaining `.dict()` sites.
