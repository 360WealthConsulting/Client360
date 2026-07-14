# Client360 Deployment Runbook (Release 0.9.9)

Operational guide for deploying and running Client360. This adds no business
behavior; it documents the readiness scaffolding shipped in Phase 7 and the
production gates carried from earlier phases.

## 1. Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `CLIENT360_ENVIRONMENT` | recommended | `production` enables strict mode (secret required, HTTPS-only cookies). Default `development`. |
| `DATABASE_URL` | yes (prod) | PostgreSQL connection string. |
| `SESSION_SECRET` | **yes in production** | Session-cookie signing key. Startup **fails** in production if unset; development uses a marked insecure fallback and logs a warning. |
| `MICROSOFT_TOKEN_KEY` | yes (for M365) | Fernet key encrypting Microsoft OAuth token caches. Without it, sync and token decryption are disabled. **Back it up separately from the database.** |

At startup the app logs a warning for each missing/insecure value
(`validate_startup_configuration()` → `configuration_warnings()`); production
boot still hard-fails on a missing `SESSION_SECRET`.

## 2. Health and readiness probes

| Endpoint | Auth | Semantics |
|---|---|---|
| `GET /health` | public | **Liveness** — DB-independent; returns `{"status":"ok"}` if the process is up. Use for restart probes. |
| `GET /readiness` | public | **Readiness** — checks DB connectivity, Alembic head (current vs expected, drift detection), scheduler state, and Microsoft sync-health. Returns **200** when ready, **503** otherwise. Use to gate traffic / rolling deploys. |

Example `/readiness` body:

```json
{
  "status": "ready",
  "checks": {
    "database": "ok",
    "migrations": {"current_head": "o5f36c4d3e2a", "expected_head": "o5f36c4d3e2a", "in_sync": true},
    "scheduler": {"running": true, "job_count": 4, "jobs": [...]},
    "microsoft_sync": {"connected": true, "last_sync_status": "ok", "last_sync_at": "..."}
  }
}
```

A `migrations.in_sync = false` means the deployed code expects a different Alembic
head than the database has — do not send traffic until migrations are applied.

## 3. Background scheduler

An in-process APScheduler (`America/New_York`) runs mail/calendar (15m), document
(30m), workflow SLA (5m), and tax reminders (daily 9am). `/readiness` reports its
running state and job count. In a multi-replica deployment run the scheduler on a
single instance (or a dedicated worker) to avoid duplicate job execution.

## 4. Database migrations

- Migrations are additive and reversible; the app maintains exactly one Alembic
  head. Apply with `alembic upgrade head` before shifting traffic.
- **Index migrations (Phase 4) use `CREATE INDEX CONCURRENTLY`** and must run
  during a low-traffic window; they are monitored because an interrupted
  `CONCURRENTLY` build can leave an INVALID index to drop and rebuild.

## 5. Backup

1. **Database:** `pg_dump -Fc -d <db> -f client360-<date>.dump` (custom format).
   Store off-host with your retention policy.
2. **`MICROSOFT_TOKEN_KEY`:** back up **separately from the database** in your
   secret manager. The DB dump contains only *encrypted* token caches; without
   the key they cannot be decrypted and every Microsoft account must reconnect.

## 6. Restore and rehearsal

Restore into a scratch database and verify recoverability with the provided
script (never against production):

```
MICROSOFT_TOKEN_KEY=<same-key-as-backup> \
  scripts/restore_rehearsal.sh client360-<date>.dump client360_restore_rehearsal
```

It restores the dump, upgrades to head, asserts a single Alembic head, prints
sentinel row counts, and runs the full test suite.

**Recorded rehearsal (this release):** restore → `alembic upgrade head` → single
head `o5f36c4d3e2a` → sentinel counts printed → **297 passed, 4 skipped**.

## 7. Monitoring and observability

- Scrape `GET /readiness` for DB, migration-drift, scheduler, and sync-health
  signals; alert on non-200 or `migrations.in_sync=false`.
- Microsoft sync-health is also persisted per account (`last_sync_status`,
  `last_sync_at`, `last_sync_error`) and surfaced on `/microsoft365/status`.
- The app logs configuration warnings at startup and cross-site rejections
  (403) with a `request_id`; mutating requests write immutable audit events.

## 8. Production configuration guidance

- Set `CLIENT360_ENVIRONMENT=production`, a strong `SESSION_SECRET`, and
  `MICROSOFT_TOKEN_KEY`; serve over HTTPS (session cookies are `https_only` in
  production).
- CSRF: state-changing requests are rejected when the `Origin` (or, absent
  Origin, the `Referer`) is cross-site.
- Run migrations before cutover; gate traffic on `/readiness`.

## 9. Remaining production deployment gates (binding through RC12)

Carried from `docs/RELEASE_0.9.9_DEPLOYMENT_GATES.md`:

1. `MICROSOFT_TOKEN_KEY` configured in every environment.
2. Key backed up separately from the database.
3. Existing Microsoft 365 connections must reconnect once after deploy.
4. A live Microsoft tenant test verifying OAuth authorization, encrypted MSAL
   cache persistence, access-token expiration, `acquire_token_silent` refresh,
   mail/calendar/document sync, and sync-health status.
5. Deferred (must remain documented): `MICROSOFT_TOKEN_KEY` rotation and removal
   of the legacy plaintext token columns; and the orphaned `app/models/` ORM
   scaffold (Phase 6 deferred technical debt).
