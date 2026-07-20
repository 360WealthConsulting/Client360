# Client360 — Project Status

_A 5-minute orientation for a new engineer or executive sponsor. Living document; updated with
each shipped increment. Companion docs: [`RELEASE_READINESS.md`](RELEASE_READINESS.md) (ship
readiness) · [`PRODUCT_DECISIONS.md`](PRODUCT_DECISIONS.md) (deferred business decisions) ·
[`../CHANGELOG.md`](../CHANGELOG.md)._

## Current version
- **Working line:** `release/0.13.0` (Sprint 2 integration). Frozen baseline: `v0.10.1-sprint1`
  on `release/0.12.0`. No `0.13.0` tag cut yet.
- **Stack:** FastAPI + SQLAlchemy Core + PostgreSQL + Alembic + Jinja SSR. Capability-based auth,
  record scoping, append-only audit + immutable timeline.

## Sprint status
- **Sprint 1 (Internal CRM):** shipped and tagged `v0.10.1-sprint1`.
- **Sprint 2 (in progress):** feature milestones below; Release Readiness tracked separately as a
  continuous checklist (not a milestone).

## Completed milestones (Sprint 2)
- **Engineering safety net** — event-loop flaky fix; Playwright E2E harness + authenticated flows
  via a dev-only sign-in provider (impossible in production); CI triggers cover fix/** + release PRs.
- **Search & data quality** — pg_trgm index-assisted search; results de-duplicated per person.
- **CRM UX** — human-readable timestamps; staff-scoped assignee; editable contact/address fields;
  timeline display styling; task-submission idempotency; communication direction.
- **Identity pipeline** — single-source promotion wired into import + on-demand backfill; Match
  Review "unresolved contacts" queue (human link/create, audited).

## Remaining milestones / work
- **Household & advanced matching (build-to-boundary done):** household-derivation *engine* built
  (policy injected, safe default); match auto-merge *not* built. Both await business policy — see
  `PRODUCT_DECISIONS.md` (PD-1, PD-2).
- **Optional in-repo polish:** `humandt` shared-templates refactor; auth-endpoint rate limiting
  (low value — external IdP); retire legacy `tasks.assigned_to` (needs a data-migration decision).

## Release readiness summary (evidence-based — see `RELEASE_READINESS.md`)
Verified: CI ✅ · E2E ✅ (advisory) · migrations reversible ✅ · 1217 tests passing ✅ ·
backup/restore mechanism ✅ (rehearsal passed on current schema) · production session hardening ✅
(fail-fast on dev-auth). Partial/outstanding: monitoring wiring 🟡 · deployment/rollback rehearsal
🟡 · performance/load test 🔴. **Based on the current checklist, no additional engineering blockers
have been identified for this stage; outstanding operational items remain before production
readiness can be claimed.**

## Outstanding operational blockers (actions outside the repository)
1. Scheduled encrypted production backups + RPO/RTO.
2. Staging deploy + rollback rehearsal.
3. Promote E2E to a required status check (branch protection).
4. Login/SSO + monitoring/alerting configured in the target environment.

## Outstanding product decisions (`PRODUCT_DECISIONS.md`)
- **PD-1** household grouping rule + auto-apply-vs-review.
- **PD-2** whether to build/enable match auto-merge + threshold.
- **PD-3** communication metadata scope (participants/duration).
- **PD-4** AD-5 regulated-insurance compliance reviewer (UNFILLED) — pre-existing, out of CRM scope.

## Known technical debt
- `humandt` registered on 3 route envs only (shared-templates refactor pending).
- Duplicate CI runs for feature→release PRs (correct, wasteful).
- ~611 baselined ruff findings (legacy) — issue #26.
- Legacy free-text `tasks.assigned_to` retained as a display fallback.

## Recommended next work
1. Optional in-repo polish (humandt refactor, rate limiting) while business decisions and ops
   actions are pending.
2. Business: resolve `PRODUCT_DECISIONS.md` PD-1/PD-2/PD-3.
3. Ops: execute the four operational blockers above, then cut a `0.13.0` release candidate.
