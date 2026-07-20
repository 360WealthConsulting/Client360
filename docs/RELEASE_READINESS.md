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
| Backup / restore verification | 🔴 | Not yet performed. Requires an ops runbook + a rehearsed restore. **Release blocker for production** (not for internal pilot). |
| Deployment verification | 🔴 | No CI/CD to staging, no zero-downtime deploy or rollback rehearsal. **Release blocker for production.** |
| Security review items | 🟡 | Capability + record-scope + append-only audit in place; dev-auth impossible in production. Outstanding: enforce `SESSION_HTTPS_ONLY`, rate limiting, dependency/secret scanning cadence. |
| Performance testing | 🔴 | Search hotspot indexed (pg_trgm); no load test at target volume yet. |
| Monitoring & health checks | 🟡 | `/health` + `/readiness` endpoints exist; not wired to monitoring/alerting. |
| Documentation status | 🟡 | CHANGELOG current; `docs/E2E.md`; this file. User guide / ops runbook / ADRs outstanding. |
| Known technical debt | 🟡 | See below. |
| Outstanding business-rule decisions | ⛔ | Household auto-derivation grouping rules; automatic match-merge thresholds. |
| Release blockers | 🔴 | Backup/restore verification; deployment/rollback verification; promote E2E to a required check. |

## Known technical debt
- `humandt` filter registered on 3 route envs only (notes/tasks/people); other surfaces show raw timestamps — proper fix is a shared-templates refactor (32 routers each build their own Jinja env).
- ~~Pre-#35 backfill~~ — resolved: `POST /matches/promote-unlinked` (button on the unresolved queue) backfills contacts imported before the wiring fix.
- `/matches/unresolved` reachable by URL but not linked from the main Match Review nav.
- Duplicate CI runs for feature→release PRs (push + pull_request both fire) — correct but wasteful.
- ~611 baselined ruff findings (legacy), tracked in issue #26.
- Legacy free-text `tasks.assigned_to` retained as a display fallback; retire after data migration.

## Outstanding business-rule / policy decisions (⛔ — engineering built up to the boundary)
- **Household auto-derivation:** which signals define a household from import data, and the confidence threshold for auto-grouping vs. review.
- **Automatic match-merge thresholds:** when an ambiguous contact should auto-merge vs. go to the (now-built) Match Review queue.

## Release blockers (must clear before production; none block internal pilot except login/SSO config)
1. Validated, encrypted backup **and** a rehearsed restore.
2. Deployment pipeline with a rehearsed rollback.
3. Promote the E2E workflow to a required status check once stable.
4. Login/SSO configured in the target environment.
