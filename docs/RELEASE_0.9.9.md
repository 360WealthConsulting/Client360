# Client360 v0.9.9 — Platform Consolidation

**Released:** 2026-07-14 · **Alembic head:** `o5f36c4d3e2a` · **Merge commit:** `592317d`

Release 0.9.9 is a consolidation and production-readiness release. It carries no
new end-user features; instead it hardens security, eliminates performance debt,
removes dead code, and adds the operational scaffolding needed to run Client360
in production. It was delivered as eight independently reviewed phases and
validated by an independent release-candidate pass (RC12) before merge.

## Release summary

- Microsoft 365 OAuth tokens are now **encrypted at rest** with a durable refresh
  lifecycle and least-privilege read-only scopes.
- The per-request client, household, portal, and workflow read paths are **index-
  bound and O(caller's book)** after 24 new foreign-key indexes and the removal of
  four verified N+1 / full-scan hot paths.
- The Microsoft Graph connector and portal provider registries were **consolidated**
  onto single canonical paths; dead debug code and unused imports were removed.
- New **operational readiness**: a `/readiness` probe, startup configuration
  validation, CSRF defense-in-depth, and a documented, rehearsed backup/restore
  procedure.

## Platform Consolidation overview

| Phase | Area | Outcome |
|---|---|---|
| 1 | Microsoft 365 token security | Encrypted MSAL cache + silent refresh; read-only scopes; sync-health |
| 2 | Graph consolidation | Single delegated Graph path; ~6 unused connector modules removed |
| 3 | Provider abstraction | One canonical `ProviderRegistry`; reserved ports kept |
| 4 | Database indexing | 24 hot-path FK indexes (CONCURRENTLY, reversible) |
| 5 | Query optimization | Four N+1 / full-scan hot paths eliminated |
| 6 | Dead code removal | Debug endpoint + unused imports removed |
| 7 | Deployment readiness | `/readiness`, config hardening, CSRF fallback, runbook |
| 8 | RC12 validation | Independent validation, 0 defects, SAFE TO MERGE |

## Security improvements

- **No plaintext OAuth tokens.** Microsoft token material is stored as an
  encrypted MSAL cache (`token_cache_encrypted`) using Fernet keyed by
  `MICROSOFT_TOKEN_KEY`; access tokens are obtained via `acquire_token_silent`.
  The crypto **fails closed** when the key is absent. Legacy plaintext columns are
  retained nullable one release for rollback and are never written.
- **Least privilege.** Delegated Graph scopes reduced to read-only
  (`User.Read, Mail.Read, Calendars.Read, Files.Read.All, Sites.Read.All`) — no
  `Mail.Send`, no `*.ReadWrite`.
- **CSRF defense-in-depth.** State-changing requests are rejected when the
  `Origin` — or, when absent, the `Referer` — is cross-site (existing behavior
  preserved).
- **Config hardening.** Production boot fails without `SESSION_SECRET`; startup
  logs loud warnings for a development fallback or a missing `MICROSOFT_TOKEN_KEY`.
- Immutable, append-only audit and record-scope authorization are **unchanged and
  re-verified** (UPDATE/DELETE on `audit_events` remain DB-rejected).

## Performance improvements

- **24 hot-path foreign-key indexes** across `people`, `tasks`, `activities`,
  `documents`, `timeline_events`, portal, workflow, and tax tables — each
  justified by a real query predicate; built with `CREATE INDEX CONCURRENTLY`.
- **N+1 elimination** (measured, constant in N):
  - tax intake dashboard: 28 → 7 queries;
  - portfolio concentration filter: 28 → 2 queries;
  - portal `/notifications` endpoint: 21 → 1 query;
  - `work_items()` authorization pushed into SQL → O(caller's book).
- All output and authorization semantics preserved (identical-output and
  negative-scope tests).

## Microsoft 365 improvements

- Single delegated Graph token path (`services/microsoft_identity.py`) with a
  durable refresh lifecycle, replacing the ~1-hour plaintext-token failure mode.
- Per-account **sync-health** (`last_sync_at`, `last_sync_status`,
  `last_sync_error`) surfaced on `/microsoft365/status` and `/readiness`.
- Unused app-only Graph client modules removed.

## Production readiness improvements

- **`GET /readiness`** — database connectivity, current-vs-expected Alembic head
  (migration-drift detection), background-scheduler state, and Microsoft
  sync-health; 200 ready / 503 not-ready. `GET /health` stays DB-independent
  liveness.
- **Backup/restore runbook** (`docs/RELEASE_0.9.9_DEPLOYMENT_RUNBOOK.md`) and
  `scripts/restore_rehearsal.sh`, with a recorded rehearsal (restore → single head
  → green suite).
- Startup configuration validation and documented environment variables.

## Validation summary (RC12)

Independent RC12 validation — **24 checks, 0 defects** (`docs/RC12_VALIDATION.md`):
compilation, whitespace, single Alembic head, 0 unused imports, clean base→head
(25 migrations), startup/OpenAPI/178 routes, v0.9.8 up→down→re-up with **sentinel
preservation**, full suite **297 passed / 4 skipped**, authorization + H13
deterministic-matching + RC-series + token-security + portal-isolation harnesses,
append-only audit immutability, no plaintext token, fail-closed crypto with
read-only scopes, live `/readiness`, 24/24 indexes planner-selected, N+1 bounded
query counts, and a recorded backup/restore rehearsal.

## Remaining deferred items

1. **Advisor notes → database** (`person_notes` migration, WP7.5) — deferred as a
   business-data/schema change.
2. **`app/models/` orphaned ORM scaffold** — broken/zero-reference; documented
   technical debt.
3. **`MICROSOFT_TOKEN_KEY` rotation** — no rotation mechanism yet.
4. **Legacy plaintext token columns** — retained nullable for rollback; removal
   deferred one release.

## Production deployment gates

1. `MICROSOFT_TOKEN_KEY` configured in every environment.
2. Key **backed up separately from the database**.
3. Existing Microsoft 365 connections must **reconnect once** after deploy.
4. **Live Microsoft tenant test** verifying OAuth authorization, encrypted MSAL
   cache persistence, access-token expiration, `acquire_token_silent` refresh,
   mail/calendar/document sync, and sync-health status (manual; not executable in
   CI).
5. Apply migrations to head `o5f36c4d3e2a`; gate traffic on `/readiness`; run the
   `CONCURRENTLY` index builds in a low-traffic, monitored window.
