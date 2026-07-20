# Client360 — Release Readiness (Sprint 2 → 0.13.0)

Living document, maintained through Sprint 2. Status legend: ✅ done · 🟡 partial/in-progress ·
🔴 not started · ⛔ blocked (business/compliance/policy).

_Last updated against `release/0.13.0`._

## Summary

| Area | Status | Notes |
|------|--------|-------|
| Database migrations reversible | ✅ | `check_migrations_reversible.sh` walks base↔head every CI run; 3 additive Sprint 2 migrations (search trgm, task idempotency, comm direction) all reversible. Single head. |
| CI status | ✅ | `Client360 CI` green on `release/0.13.0`; triggers now cover `feature/**`, `fix/**`, `release/**` push and PRs to `main`/`release/**` (#38). |
| E2E status | 🟡 | Playwright (`Client360 E2E`) green and covering login/dashboard/people/households/search/notes/tasks/communications via the dev-only auth provider. **Advisory (non-gating)** until promoted to a required check. |
| Test coverage | 🟡 | 1206 tests passing (service + route level). No coverage % gate yet; browser E2E is smoke-level. |
| Backup / restore verification | 🟡 | **Mechanism verified for 0.13.0:** `scripts/restore_rehearsal.sh` ran clean against a `pg_dump -Fc` of the current schema (head `d4c5o6m7d8i9`) — restored into a scratch DB, single Alembic head confirmed, full suite (1217) green on the restored data. Runbook: `RELEASE_0.9.9_DEPLOYMENT_RUNBOOK.md` §5–6. **Outstanding (ops):** scheduled, encrypted production backups + a documented RPO/RTO. |
| Deployment verification | 🟡 | Runbook + deployment gates exist (`RELEASE_0.9.9_DEPLOYMENT_RUNBOOK.md`, `RELEASE_0.9.9_DEPLOYMENT_GATES.md`); migrations reversible (mechanism for rollback). **Outstanding (ops):** a staging deploy + rollback rehearsal on the target infra. |
| Security review items | 🟡 | Capability + record-scope + append-only audit; CSRF same-origin check; secret-scan gate in CI. `SESSION_SECRET` required in production and `SESSION_HTTPS_ONLY` auto-enforced there (derived from environment); production **fails fast** if `CLIENT360_DEV_AUTH` is set. Outstanding: rate limiting (low priority — external IdP), dependency-vuln scan cadence. |
| Performance testing | 🔴 | Search hotspot indexed (pg_trgm); no load test at target volume yet. |
| Monitoring & health checks | 🟡 | `/health` + `/readiness` endpoints exist; not wired to monitoring/alerting. |
| Documentation status | 🟡 | CHANGELOG current; `docs/E2E.md`; this file. User guide / ops runbook / ADRs outstanding. |
| Known technical debt | 🟡 | See below. |
| Outstanding business-rule decisions | ⛔ | Tracked in [`docs/PRODUCT_DECISIONS.md`](PRODUCT_DECISIONS.md) (PD-1 household grouping rule, PD-2 match auto-merge, PD-3 comm metadata, PD-4 AD-5 compliance). |
| Release blockers | 🔴 | Backup/restore verification; deployment/rollback verification; promote E2E to a required check. |

## Known technical debt
- `humandt` filter registered on 3 route envs only (notes/tasks/people); other surfaces show raw timestamps — proper fix is a shared-templates refactor (32 routers each build their own Jinja env).
- ~~Pre-#35 backfill~~ — resolved: `POST /matches/promote-unlinked` (button on the unresolved queue) backfills contacts imported before the wiring fix.
- ~~`/matches/unresolved` not linked from Match Review nav~~ — resolved: linked from `/matches`.
- Duplicate CI runs for feature→release PRs (push + pull_request both fire) — correct but wasteful.
- ~611 baselined ruff findings (legacy), tracked in issue #26.
- Legacy free-text `tasks.assigned_to` retained as a display fallback; retire after data migration.

## Outstanding business-rule / policy decisions (⛔ — engineering built up to the boundary)
- **Household auto-derivation:** the *engine* is built (`app/services/household_derivation.py`, injected policy, safe no-op default, dry-run, tested) and a candidate `group_by_normalized_address` policy is provided but **not enabled**. Awaiting the firm's decision on the grouping signal and auto-apply vs. review.
- **Automatic match-merge thresholds:** the Match Review queue + resolution/backfill are built; only the *auto-merge* threshold policy (when to merge without a human) awaits a business decision.

## Release blockers (must clear before production; all are operational actions outside the repo)
1. Scheduled, encrypted production backups + documented RPO/RTO (restore mechanism verified; §5–6 runbook).
2. Staging deploy + rollback rehearsal on the target infra (runbook + reversible migrations in place).
3. Promote the E2E workflow to a required status check (branch-protection change — repo admin).
4. Login/SSO configured in the target environment; wire `/health`,`/readiness` to monitoring/alerting.
