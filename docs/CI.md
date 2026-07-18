# Client360 — CI & Quality Gates (E1.4)

Continuous integration validates every push to `main`, `feature/**`, `release/**`
and every PR to `main`. The pipeline is `.github/workflows/ci.yml`
(job `build`). It provisions a disposable `postgres:16` (`client360_ci`) because
`app/db.py` reflects the schema at import — the suite needs a real, migrated DB.

## Quality gates (in order)
| Gate | Command | Fails when |
|---|---|---|
| Dependency consistency | `python -m pip check` | pinned deps conflict |
| Secret hygiene | `scripts/check_secrets.py` | a real env file or credential is committed |
| Lint (Ruff gate) | `scripts/ruff_gate.py` | a change **adds** a Ruff violation |
| Ruff baseline may not grow | `scripts/ruff_gate.py --assert-not-grown` (PR) | the baseline is regenerated to bury a new finding |
| CHANGELOG lint | `scripts/check_changelog.py` | changelog structure drifts |
| Compile | `compileall app tests migrations` | any file fails to compile |
| Import boundaries | `lint-imports` | a declared import-boundary contract is broken |
| Whitespace (PR diff) | `git diff --check` | the PR adds whitespace errors |
| Single Alembic head | `scripts/check_migration_heads.sh` | the graph has >1 head |
| Migrations reversible | `scripts/check_migrations_reversible.sh` | any downgrade is broken (leaves schema at head) |
| Schema consistency | `scripts/check_schema_consistency.py` | >1 head, a declared table is missing, or a table lacks a PK |
| Test-DB safety guard | `pytest --collect-only` vs a `_production` name | the suite would collect against a non-disposable DB |
| Tests | `python -m pytest` | any test fails |

On failure, `pytest-results.xml` + `.demo-server.log` are uploaded as artifacts.

## Quality-gate philosophy
Existing technical debt does not block CI. Gates are **baseline-aware**: they fail
on **new** problems, not pre-existing ones.
- **Ruff:** the legacy backlog is recorded in `docs/ruff-baseline.json`
  (`scripts/ruff_gate.py`); only new violations fail, and the baseline may only
  shrink.
- **Migrations/schema:** validated forward-only; history is never rewritten
  (see [DATABASE.md](DATABASE.md)).
- **Import boundaries:** contracts are added gradually (ADR-013); each is added
  only when it already passes.

**Requirements for a green build:** no new regressions, no new lint violations,
no new migration/schema inconsistencies, and all newly added tests passing.

## Running the pipeline locally
```bash
source .venv/bin/activate
python -m pip check
python scripts/check_secrets.py
python scripts/ruff_gate.py
python -m compileall app tests migrations
lint-imports
scripts/check_migration_heads.sh
scripts/check_migrations_reversible.sh            # disposable DB
DATABASE_URL=postgresql://localhost/client360_test python scripts/check_schema_consistency.py
scripts/test.sh run                               # full suite (resets the test DB)
```

## Accepted technical debt (documented, non-blocking)
- **mypy is not a CI gate.** It is configured as a permissive baseline
  (`pyproject.toml`); the legacy type backlog is not enforced. New code is
  expected to type-check; wiring a non-blocking mypy step is a future improvement.
- **Secret scanning is a lightweight baseline** (`scripts/check_secrets.py`):
  high-signal patterns only. A full scanner (e.g., gitleaks/trufflehog) is a
  future improvement.
- **No container-image build in CI.** Build reproducibility rests on pinned
  dependencies + `pip check`; building `infrastructure/Dockerfile` in CI is a
  future improvement.
- **Ruff legacy baseline** (`docs/ruff-baseline.json`) records pre-existing
  findings, reduced incrementally.

## Troubleshooting
| Symptom | Fix |
|---|---|
| "Ruff baseline may not grow" fails | You added a violation; fix it in your changed files (do not regenerate the baseline) |
| Import boundaries fails | Your change imports across a forbidden boundary; use the module's public API (see [architecture/MODULE_MAP.md](architecture/MODULE_MAP.md)) |
| Schema consistency fails | A declared table is missing or a new table lacks a PK; see [DATABASE.md](DATABASE.md) |
| Multiple Alembic heads | Rebase your migration onto head |
| Secret hygiene fails | Remove the committed secret/env file; store secrets in the vault, never in Git |
| Tests can't reach the DB | CI provisions `client360_ci`; locally use `scripts/test.sh` |
