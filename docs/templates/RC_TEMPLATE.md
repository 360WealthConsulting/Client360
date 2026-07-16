# {{RC_ID}} — Release Candidate Validation (Release {{VERSION}}{{TITLE}})

**Scope:** {{SCOPE}}
**Baseline:** `{{BASELINE_REF}}`
**Candidate:** `{{CANDIDATE_SHA}}` on branch `{{BRANCH}}`
**CI:** {{CI_STATUS}}
**Validator:** {{VALIDATOR}}
**Date:** {{DATE}}

> Merge gate. {{MERGE_GATE}}

---

## 0. Final recommendation

{{RECOMMENDATION}}

## 1. Build, suite, and static gates

| Check | Method | Result |
|---|---|---|
| Full test suite | `scripts/test.sh run` | {{SUITE}} |
| Compile | `python -m compileall app tests migrations` | {{COMPILEALL}} |
| Whitespace | `git diff --check` | {{DIFFCHECK}} |
| Ruff gate | `python scripts/ruff_gate.py` | {{RUFF}} |
| CHANGELOG | `python scripts/check_changelog.py` | {{CHANGELOG}} |

## 2. Migration integrity

| Check | Method | Result |
|---|---|---|
| Exactly one head | `scripts/check_migration_heads.sh` | {{SINGLE_HEAD}} |
| Reversible (down→base→head) | `scripts/check_migrations_reversible.sh` | {{REVERSIBLE}} |
| Schema at head | `scripts/check_schema_at_head.sh` | {{AT_HEAD}} |
| Alembic head | — | `{{ALEMBIC_HEAD}}` |

## 3. Domain validation

<!-- FILL: per-release functional checks (routes, authorization, record scope,
     data). Add one subsection per domain touched. -->

## {{DEFECTS_N}}. Defects

<!-- FILL: numbered defects with disposition, or "None." -->

## {{VERDICT_N}}. Verdict

{{VERDICT}}
