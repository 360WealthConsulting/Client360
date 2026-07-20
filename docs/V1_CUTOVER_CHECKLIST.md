# Client360 — Version 1.0 Production Cutover Checklist

**Operational execution checklist** for transitioning Client360 from development into production
use. This is **not** an engineering, deployment-runbook, or design document — it coordinates the
*activities and ownership* required for a safe cutover, and is executable by an operations team that
did not build the software.

- Deployment mechanics: `docs/RELEASE_0.9.9_DEPLOYMENT_RUNBOOK.md` (§ referenced per item).
- Release/ops scripts: `scripts/release.sh` (tag), `scripts/deploy.sh` (deploy orchestration),
  `scripts/smoke.sh` (post-deploy smoke), `scripts/rollback.sh` (migration rollback),
  `scripts/restore_rehearsal.sh` (restore).
- Readiness state / owners: `docs/RELEASE_READINESS.md`, `docs/RC_READINESS.md`,
  `docs/V1_RISK_REGISTER.md`.

**Owner placeholders** (repository names no individual — assign before cutover):
`[DEPLOY-OWNER]` · `[MONITOR-OWNER]` · `[BACKUP-OWNER]` · `[INCIDENT-OWNER]` · `[SUPPORT-OWNER]` ·
`[BUSINESS-OWNER]` · `[EXEC-SPONSOR]`.

Status values: **Pending** · **In progress** · **Complete** · **Blocked** · **N/A**.

---

## Phase 1 — Release Preparation

| # | Task | Status | Owner | Evidence | Completed | Notes |
|---|------|--------|-------|----------|-----------|-------|
| 1.1 | Confirm release version number (e.g. `1.0.0`) | Pending | `[EXEC-SPONSOR]` | CHANGELOG currently `[Unreleased]` | ____ | Version decision required before tagging |
| 1.2 | Promote CHANGELOG `[Unreleased]` → dated version + publish release notes | Pending | `[DEPLOY-OWNER]` | `CHANGELOG.md`; `scripts/check_changelog.py` | ____ | |
| 1.3 | Create annotated release tag | Pending | `[DEPLOY-OWNER]` | `scripts/release.sh <version>` (guarded; 9 preconditions) | ____ | Tag then push deliberately |
| 1.4 | Confirm deployment window (date/time, freeze) | Pending | `[DEPLOY-OWNER]` | — | ____ | Communicate to all owners |
| 1.5 | Notify stakeholders of the cutover window | Pending | `[BUSINESS-OWNER]` | — | ____ | Staff + owners |
| 1.6 | Confirm rollback decision authority (who can call a rollback) | Pending | `[EXEC-SPONSOR]` | Rollback mechanism: `scripts/rollback.sh` | ____ | Name the person empowered to abort |

## Phase 2 — Production Readiness

| # | Task | Status | Owner | Evidence | Completed | Notes |
|---|------|--------|-------|----------|-----------|-------|
| 2.1 | Production infrastructure available (app host, managed PostgreSQL, TLS) | Pending | `[DEPLOY-OWNER]` | Runbook §1–2 | ____ | |
| 2.2 | Authentication (SSO/IdP) configured in the target environment | Pending | `[DEPLOY-OWNER]` | `docs/AUTHENTICATION.md`; `config/.env.example` | ____ | Verify a real login before go-live |
| 2.3 | Required environment variables set (`DATABASE_URL`, `SESSION_SECRET`, `MICROSOFT_TOKEN_KEY`, `CLIENT360_ENVIRONMENT=production`) | Pending | `[DEPLOY-OWNER]` | Runbook §1; startup fails fast if misconfigured | ____ | `CLIENT360_DEV_AUTH` must be unset in prod |
| 2.4 | Monitoring/alerting enabled against `/health` + `/readiness` | Pending | `[MONITOR-OWNER]` | Probes verified robust (`RELEASE_READINESS`) | ____ | Wire probes to the alerting system |
| 2.5 | Scheduled, encrypted backups configured (+ documented RPO/RTO) | Pending | `[BACKUP-OWNER]` | Runbook §5 (`pg_dump -Fc`) | ____ | |
| 2.6 | Restore verification acknowledged | Complete (mechanism) | `[BACKUP-OWNER]` | `scripts/restore_rehearsal.sh` — rehearsed clean on current schema | ____ | Re-run against the production dump once taken |
| 2.7 | Deployment owner present | Pending | `[DEPLOY-OWNER]` | Owner is a role placeholder today | ____ | Assign a named individual |
| 2.8 | Incident owner / on-call present | Pending | `[INCIDENT-OWNER]` | On-call ownership not yet assigned | ____ | Assign a named individual |
| 2.9 | Support owner present | Pending | `[SUPPORT-OWNER]` | `USER_GUIDE.md` "getting help" | ____ | Assign a named individual |
| 2.10 | Escalation contacts confirmed | Pending | `[INCIDENT-OWNER]` | No escalation path documented | ____ | Define who is called, in order |

## Phase 3 — Deployment

| # | Task | Status | Owner | Evidence | Completed | Notes |
|---|------|--------|-------|----------|-----------|-------|
| 3.1 | Deploy the production release | Pending | `[DEPLOY-OWNER]` | `scripts/deploy.sh --url <prod>` (migrate → start → smoke → rollback-on-failure); Runbook §3–4 | ____ | |
| 3.2 | Execute post-deploy smoke validation | Pending | `[DEPLOY-OWNER]` | `scripts/smoke.sh <prod-url>` | ____ | Must exit 0 |
| 3.3 | Verify health endpoints | Pending | `[DEPLOY-OWNER]` | `GET /health` → 200; `GET /readiness` → 200 `ready` | ____ | |
| 3.4 | Verify scheduled jobs running | Pending | `[MONITOR-OWNER]` | `/readiness` reports scheduler state (job count/next-run) | ____ | |
| 3.5 | Verify authentication (a real user can sign in) | Pending | `[DEPLOY-OWNER]` | SSO login | ____ | |
| 3.6 | Verify database migration status (single head, in sync) | Pending | `[DEPLOY-OWNER]` | `/readiness` `migrations.in_sync=true`; `scripts/check_migration_heads.sh` | ____ | |

## Phase 4 — Business Acceptance

| # | Task | Status | Owner | Evidence | Completed | Notes |
|---|------|--------|-------|----------|-----------|-------|
| 4.1 | Staff login verification (each role can sign in) | Pending | `[BUSINESS-OWNER]` | — | ____ | |
| 4.2 | Key workflows validated (search → profile → notes → communications → tasks → households) | Pending | `[BUSINESS-OWNER]` | `docs/USER_GUIDE.md` steps | ____ | Business UAT, not engineering E2E |
| 4.3 | Acceptance approval (user-acceptance sign-off) | Pending | `[EXEC-SPONSOR]` | — | ____ | Written sign-off |
| 4.4 | Support team notified and ready | Pending | `[SUPPORT-OWNER]` | — | ____ | |
| 4.5 | Go-live announcement to staff | Pending | `[BUSINESS-OWNER]` | — | ____ | |

## Phase 5 — Stabilization

| # | Task | Status | Owner | Evidence | Completed | Notes |
|---|------|--------|-------|----------|-----------|-------|
| 5.1 | Monitor production (first hours/days) | Pending | `[MONITOR-OWNER]` | Alerting on `/readiness` | ____ | Define the watch window |
| 5.2 | Review alerts / triage anomalies | Pending | `[INCIDENT-OWNER]` | Monitoring system | ____ | |
| 5.3 | Confirm the first scheduled backup executed successfully | Pending | `[BACKUP-OWNER]` | Backup job logs | ____ | |
| 5.4 | Capture production issues (intake/triage) | Pending | `[SUPPORT-OWNER]` | Request-ids in the audit log for tracing | ____ | |
| 5.5 | Formal release closeout | Pending | `[EXEC-SPONSOR]` | This checklist fully completed | ____ | Sign off; archive the completed checklist |

---

## Cutover go / no-go
Proceed to **Phase 3 (Deployment)** only when **all of Phase 2 is Complete**. Abort and invoke
`scripts/rollback.sh` (authority per item 1.6) if any Phase 3 verification fails. Declare go-live
only after **Phase 4** acceptance sign-off (item 4.3).
