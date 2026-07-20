# Client360 — Release Candidate Readiness

Defines what is required to cut the first Version 1.0 **Release Candidate** and what evidence proves
each item. Goal: **another engineer can deploy this RC confidently using only the repository.**

Status: ✅ verified in-repo · 🟡 mechanism present, execution/wiring is Operations · 🔴 not built ·
⛔ external dependency. Each row: **owner · evidence · verification method · status**.

_Baseline: `release/0.13.0` @ current tip._

| # | Item | Owner | Evidence (in repo) | Verification method | Status |
|---|------|-------|--------------------|---------------------|--------|
| RC-1 | **Build reproducibility** | App/Release Eng | Pinned `requirements.txt` / `requirements-dev.txt` / `requirements-e2e.txt`; `pip check` gate in CI | CI job "Dependency consistency (pip check)" | 🟡 (deps pinned + checked; no lockfile/container digest) |
| RC-2 | **Artifact creation** | Release Eng | — (app runs from source; no container image / wheel) | — | 🔴 (see RE-1 below) |
| RC-3 | **Release tagging** | Release Eng | `scripts/release.sh` — 9 guarded preconditions then annotated tag | `scripts/release.sh <version> --dry-run` | ✅ mechanism |
| RC-4 | **Deployment automation** | Release Eng / Ops | Runbook `RELEASE_0.9.9_DEPLOYMENT_RUNBOOK.md` (manual); no CD pipeline | Follow runbook §1–4 | 🟡 (documented; not automated — RE-2) |
| RC-5 | **Rollback automation** | Release Eng / Ops | Reversible migrations + `scripts/rollback.sh` (migration downgrade helper) + runbook | `scripts/rollback.sh --to <rev> --dry-run` | ✅ mechanism |
| RC-6 | **Smoke tests (post-deploy)** | Release Eng | `scripts/smoke.sh` — checks a running instance's `/health`, `/readiness`, static assets, auth gate | `scripts/smoke.sh <base-url>` | ✅ mechanism (verified locally) |
| RC-7 | **Monitoring validation** | Release Eng / Ops | `/readiness` reports DB, migration-drift, scheduler, sync; 503 when not ready | `curl <url>/readiness` | ✅ probe verified; ⛔ alerting wiring is Ops |
| RC-8 | **Backup validation** | Ops | `pg_dump -Fc` documented (runbook §5) | Runbook §5 | 🟡 mechanism; ⛔ scheduled prod backups are Ops |
| RC-9 | **Restore validation** | Release Eng | `scripts/restore_rehearsal.sh` — rehearsed clean against current schema | `scripts/restore_rehearsal.sh <dump> <scratch-db>` | ✅ verified |
| RC-10 | **Environment validation** | Release Eng | `app/config.py` — fails fast in prod on missing `SESSION_SECRET` / set `CLIENT360_DEV_AUTH`; warns on dev fallbacks | `validate_startup_configuration()` at boot; `tests/test_startup_safety.py` | ✅ mechanism |
| RC-11 | **Acceptance criteria** | App/Release Eng | `V1_RELEASE_PLAN.md` §2 measurable criteria | Review criteria table | ✅ engineering; ⛔ ops rows pending |

## Category status (independent, per the four-area model)
- **Application Engineering:** implementation complete for the V1.0 CRM scope (see `V1_RELEASE_PLAN.md` E1–E5/T1/D1 = ✅).
- **Release Engineering:** in progress — tagging ✅, restore ✅, smoke ✅, rollback ✅, env-validation ✅; **artifact packaging (RC-2)** and **deployment automation (RC-4)** are the remaining in-repo gaps.
- **Operations:** external — scheduled backups, staging/prod deploy, monitoring wiring, SSO, infra.
- **Product & Compliance:** `PRODUCT_DECISIONS.md` (PD-1/2/3 non-blocking; PD-4 AD-5 out of scope).

## Remaining Release Engineering work (in-repo, prioritized)
- **RE-1 (RC-2) Artifact/build packaging** — add a reproducible build (e.g. a Dockerfile pinned to
  `python:3.12` + the pinned requirements) so the RC is a versioned artifact, not "clone + run".
- **RE-2 (RC-4) Deployment automation** — a deploy workflow/script skeleton wrapping the runbook
  steps (migrate → deploy → smoke → (rollback on smoke failure)).

## Definition of "RC ready"
RC-3, RC-5, RC-6, RC-9, RC-10 ✅ (done) **and** RC-1/RC-2 (reproducible artifact) **and** RC-4
(deployment automation) complete in-repo. Operational rows (RC-7 wiring, RC-8 scheduling) and the
`V1_RELEASE_PLAN` operational criteria are executed by Operations against real infrastructure; they
are prerequisites for *promoting* the RC to production, not for *cutting* it.
