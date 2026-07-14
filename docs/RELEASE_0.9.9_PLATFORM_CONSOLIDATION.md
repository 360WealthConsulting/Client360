# Release 0.9.9 — Platform Consolidation (Technical Design)

**Status:** technical design — submitted for review. No application code written.
**Baseline:** Release v0.9.8 (`main` @ `8d27e95`, Alembic head `l2c03f1e0d9b`).
**Governing design document:** `docs/PRODUCTION_ARCHITECTURE.md` — the
authoritative Client360 architecture. Every change in this release must conform to
it; the relevant sections are cross-referenced below (token protection §9/§19,
Graph integration §8, background jobs §10, performance/scalability §23, backup/DR
§20, monitoring §21, deployment topology §22, ADR-13/ADR-14).
**Authoritative backlog:** `docs/VERSION_1_PROGRESS_REVIEW.md`.
**Nature:** Release 0.9.9 — Platform Consolidation is the first
platform-consolidation release (Epic 7, part 1) on the revised road to Version
1.0. No new product features; this release retires the highest-risk security,
reliability, performance, and dead-code debt surfaced by RC8/RC9 and the Version
1.0 progress review.

---

## 1. Objectives

1. **Eliminate the highest-severity open security risk (RC8/RC9 H10):** encrypt
   Microsoft 365 OAuth tokens at rest and implement a real token-refresh
   lifecycle so the integration stops failing silently ~1 hour after connect.
2. **Reduce Microsoft Graph blast radius:** request least-privilege (read-only)
   scopes and add sync-health observability.
3. **Consolidate the Microsoft Graph integration** onto a single provider path and
   remove the ~600-line unused parallel connector.
4. **Retire dead/orphaned modules** and a leftover debug endpoint.
5. **Remove the worst performance cliffs (RC8/RC9 H15–H20):** eliminate the N+1
   dashboard patterns and add the missing foreign-key / hot-column indexes.
6. **Add deployment-readiness scaffolding:** a health/readiness endpoint,
   sync-health surfacing, config/secret hardening, and a documented
   backup/restore procedure and rehearsal.

**Explicitly out of scope (deferred):** new tax features (Epic 5 Sprints 5.5–5.8);
the full database CHECK-constraint/`jsonb` pass and API-envelope standardization
(Epic 7 part 2, Release 0.9.13); external provider/AI work (Epic 6). Advisor-notes
migration to the database is *related* and may ride along if low-risk, but is not
a core objective here.

---

## 2. Architecture

The release touches four subsystems, each with a clear seam:

- **Microsoft 365 identity/token layer** (`app/routes/microsoft365_oauth.py`,
  `app/jobs/microsoft_*_sync.py`, `microsoft_accounts` table): introduce an
  encryption boundary and an MSAL-backed refresh path; no change to the Graph
  request logic beyond "obtain a valid token" now going through one helper.
- **Graph provider consolidation** (`app/connectors/microsoft365/*`): keep the one
  used module (`config.py`) and remove the unused app-only client tree.
- **Performance layer** (`app/services/work_management.py`,
  `app/services/tax_intake.py`, `app/portal/service.py`,
  `app/services/portfolio.py`, plus one indexing migration): push authorization and
  filtering into SQL; replace per-row loops with bulk queries; add indexes.
- **Operational surface** (`app/routes/dashboard.py` or a new `ops` route,
  `app/config.py`): a readiness endpoint, sync-health, and secret/CSRF hardening.

**Guiding constraints (unchanged platform principles):** single linear Alembic
head; additive/reversible migrations with sentinel preservation; immutable audit;
least privilege; no second authorization or provider engine; behavior-preserving
where practical (existing tests must stay green except where a fix intentionally
changes an authorization/scope result, which must be covered by a new test).

---

## 3. Security Improvements

- **Microsoft 365 token protection (H10)** — §4.
- **Refresh-token lifecycle (H10)** — §5.
- **Least-privilege Graph scopes:** `DELEGATED_SCOPES` currently requests
  `Mail.Send`, `Contacts.ReadWrite`, `Files.ReadWrite` while the wired code only
  reads. Reduce to read-only (`User.Read`, `Mail.Read`, `Calendars.Read`,
  `Files.Read.All`, `Sites.Read.All`) plus `offline_access` (required for refresh).
  This shrinks the blast radius of any leaked token.
- **Session-secret hardening:** `SESSION_SECRET` currently falls back to a
  hardcoded development value outside `production`. Emit a loud startup warning and
  document that any internet-exposed environment must set it; keep the
  production-required guard.
- **CSRF hardening (defense-in-depth):** the current Origin-only check fails open
  when the header is absent. Add a `Referer` fallback and document a token-based
  option; low-risk given `SameSite=lax` cookies, so scoped as a small hardening,
  not a rewrite.
- **Sensitive-read audit (optional, low-risk):** add audit events for GETs of tax
  return detail and portal message threads if it fits the existing audit model
  without perturbing hot paths; otherwise defer with a note.

---

## 4. Microsoft 365 Token Protection

**Problem (verified):** `microsoft_accounts.access_token` / `refresh_token` are
plaintext `Text` columns; a DB read (backup leak, insider access) exposes live
mailbox/drive credentials.

**Design:**

- **Application-level encryption with Fernet** (`cryptography==49.0.0` is already a
  dependency — no new package). A `MICROSOFT_TOKEN_KEY` (Fernet key, sourced from
  the environment / secrets manager, never committed) encrypts token material
  before persistence and decrypts on read.
- **Encrypt the MSAL token cache, not raw bearer tokens.** Rather than storing a
  bare `access_token`, persist the MSAL `SerializableTokenCache` blob (which holds
  the refresh token and account) encrypted in a new column
  `token_cache_encrypted` (`Text`/`BYTEA`); the plaintext `access_token`/
  `refresh_token` columns are retired (or nulled and ignored). A single
  `token.py` helper encapsulates `encrypt(bytes) -> str` / `decrypt(str) -> bytes`
  and never logs plaintext.
- **Key management:** the key is a reference to a secrets-managed value; the design
  documents key rotation as re-encrypting the cache blob (each account row
  independently), so rotation is a background pass, not a downtime event.
- **Fail-closed:** if `MICROSOFT_TOKEN_KEY` is absent, token persistence/refresh
  raises a clear configuration error rather than silently storing plaintext.

**Migration impact:** add `token_cache_encrypted` (nullable) and drop reliance on
the plaintext columns (kept nullable for one release for safe rollback, then
removed in a later release). Existing rows have no usable token today (refresh was
never captured), so **existing connected accounts must re-connect once** — this is
not a new regression (they already expire hourly).

---

## 5. Refresh-Token Lifecycle

**Root cause (verified):** `DELEGATED_SCOPES` omits `offline_access`, so MSAL never
returns a refresh token; and the callback hardcodes `refresh_token=None`. Every
sync job then raises `RuntimeError` ~60–90 minutes after connect.

**Design:**

- **Request `offline_access`** so MSAL issues a refresh token, and **persist the
  MSAL token cache** (encrypted, §4) on connect instead of a bare access token.
- **Silent refresh before each Graph call:** a shared
  `get_microsoft_access_token(account) -> str` helper loads the encrypted cache
  into an MSAL `ConfidentialClientApplication`, calls
  `acquire_token_silent(scopes, account=...)` (which transparently refreshes using
  the refresh token when the access token is stale), re-persists the (possibly
  updated) cache, and returns a valid bearer token. The three sync jobs call this
  helper instead of reading `account["access_token"]` directly.
- **Graceful degradation preserved:** if silent refresh fails (revoked consent,
  expired refresh token), the helper raises the existing "reconnect Microsoft 365"
  `RuntimeError`, which the scheduler already catches — so the failure mode is
  unchanged, but it now only occurs on genuine re-consent, not hourly.
- **Sync-health recording:** each sync job records `last_sync_at`,
  `last_sync_status`, and `last_sync_error` (new columns) so operators can see the
  integration's health (§13).
- **No new refresh scheduler:** refresh happens lazily at call time
  (`acquire_token_silent`), which is the MSAL-idiomatic approach and needs no extra
  timer.

---

## 6. Provider Abstraction Cleanup

- **One Graph provider path.** The wired implementation is the delegated
  auth-code-flow used by the OAuth route and sync jobs. Consolidate all Graph token
  acquisition behind the single `get_microsoft_access_token` helper (§5) and the
  retained `config.py`; remove the competing app-only client (§7).
- **Tax filing provider (`app/services/tax_filing_providers.py`):** it is orphaned
  (never imported). Wiring it into `record_filing` is **Epic 5 Sprint 5.6** scope,
  not this release. Options: (a) leave it with an explicit docstring pointer to
  5.6, or (b) remove it and re-introduce in 5.6. **Recommendation: keep it,
  add a docstring stating it is reserved for Sprint 5.6**, to avoid churn — but
  this decision is called out for reviewer approval. It is the one "provider
  abstraction" not removed here.

---

## 7. Graph Connector Consolidation

- `app/connectors/microsoft365/` is 8 files / ~645 lines. Only `config.py`
  (`get_microsoft365_config`) is used (by `microsoft365_oauth.py` and
  `microsoft365.py`). The remaining files (`auth.py`, `graph.py`, `calendar.py`,
  `mail.py`, `contacts.py`, `sharepoint.py`) implement an **app-only
  client-credentials** Graph client that is architecturally incompatible with the
  wired **delegated** flow and is imported by nothing.
- **Action:** remove the six unused modules; keep `config.py` (and `__init__.py`
  trimmed to what remains). Verify via grep that nothing outside the directory
  imports the removed symbols before deletion.

---

## 8. Performance Optimization

Target the four confirmed hot paths (RC8/RC9 H15–H19):

- **`work_items()` (H15):** currently `select(tasks)` and a `workflow_steps` join
  with **no WHERE**, filtered in Python after loading every row — on every My Work /
  Team Work / queue / metrics request. Rewrite to push the authorization predicate
  (an `EXISTS` against `record_assignments`, plus person/household/team scope) and
  the priority/status/due filters into SQL, so the query is O(caller's book), not
  O(all firm work). `production_dashboard()` already demonstrates the target
  pattern.
- **Tax intake `staff_dashboard()` / `api_detail()` (H16):** replace the per-return
  `intake_detail()` loop (1+6N queries) with bulk `WHERE … IN (return_ids)`
  queries; make `api_detail()` authorize via a cheap id-set check instead of
  recomputing the whole dashboard.
- **Portal `dashboard()` fan-out (H17):** thread a single `portal_scope()` result
  through `dashboard()` and its sub-calls (currently computed 4×); give the narrow
  portal endpoints (`/documents`, `/requests`, `/tasks`, `/notifications`)
  dedicated single-purpose queries instead of calling `dashboard()` wholesale.
- **Portfolio concentration search (H19):** compute the concentration ratio in SQL
  (window/subquery over `account_holdings`) instead of the per-row
  `get_person_portfolio()` loop (up to 7 queries/row); combine with a `LIMIT`.
- **Unbounded firm-wide lists (H18):** add `limit`/`offset` (sane default) to the
  `/activities` and `/tasks` dashboards and the relationship/portfolio search
  services.

Correctness guardrail: each rewrite must preserve the exact authorization/scope
semantics of the current Python-side filtering, proven by diffing output against
the current implementation on a fixture set and by new negative-scope tests.

---

## 9. Database Indexing

- Add the missing foreign-key / hot-filter indexes identified by RC9 H20 (~48
  total; prioritize the confirmed hot-path set first):
  `people.household_id`; `tasks.person_id`; `activities.person_id`;
  `documents.person_id`; `timeline_events.person_id` and `.household_id`;
  `household_relationships.person_id`; `portal_notifications.portal_account_id`;
  `portal_threads.household_id` and `.person_id`; `tax_engagements.person_id` and
  `.household_id`; `audit_events.actor_user_id` and `(entity_type, entity_id)`;
  plus the remaining FK columns in a second batch.
- **Online safety:** build with `CREATE INDEX CONCURRENTLY` inside an Alembic
  `autocommit_block()` (the default transactional migration block cannot create
  indexes concurrently), and split into small migrations if any single build risks
  a long lock. Validate that the query planner actually selects each new index.

---

## 10. N+1 Elimination

The N+1 rewrites are enumerated in §8 (work_items, tax intake dashboard, portal
fan-out, portfolio concentration). Method for each: (1) capture the current query
count against a seeded fixture; (2) rewrite to bulk/SQL-filtered queries; (3)
assert the new query count is bounded (independent of N) and the result set is
identical. These are behavior-preserving performance changes, not feature changes.

---

## 11. Dead Code Removal

- The six unused Microsoft Graph connector modules (§7).
- `POST /timeline/test` — a leftover debug endpoint that writes a synthetic
  timeline event into a real record (`person_id=1`). Remove.
- `app/portal/signatures.py` — dead e-signature module (never routed). **Decision
  for reviewer:** remove now, or defer to Sprint 5.6 which may wire e-signature
  into e-file authorization. **Recommendation: keep with a docstring pointer to
  5.6** (parallel to the filing-provider decision, §6) to avoid churn; remove only
  if 5.6 is deprioritized.
- Unused imports flagged by RC8 in `app/security/policy.py` and
  `app/security/service.py`.

---

## 12. Deployment Readiness

- **Readiness/health endpoint:** a `/readiness` (or extend `/health`) endpoint that
  checks database connectivity and reports the Microsoft 365 sync-health summary
  and the Alembic head, for load-balancer and operator use. `/health` stays a
  liveness check (no DB dependency) so it works during a DB outage.
- **Config hardening:** startup warning when `SESSION_SECRET` uses the dev
  fallback; fail-closed when `MICROSOFT_TOKEN_KEY` is required but missing;
  document all required environment variables in one place.
- **Scheduler multi-replica note:** document that the in-process scheduler must run
  on a single instance (or gain leader election) before horizontal scaling; a full
  leader-election implementation is flagged as a follow-up, not built here, to keep
  the release bounded — but the risk is documented as a deployment constraint.

---

## 13. Monitoring

- **Microsoft 365 sync-health:** persist `last_sync_at` / `last_sync_status` /
  `last_sync_error` per account (§5) and surface them on `/microsoft365/status`
  (which today only reports config presence) and the readiness endpoint.
- **Structured operational signals:** ensure sync jobs and the scheduler emit
  structured log lines (job name, counts, duration, outcome) at consistent levels
  so an external log/metrics collector can alert; the design specifies the fields,
  not a specific vendor integration.
- **Denied-mutation and sync-failure visibility:** the immutable audit log already
  records denied high-risk mutations (0.9.7); document the queries operators use to
  monitor them.

---

## 14. Backup and Restore

- **Documented procedure:** a `pg_dump`/`pg_restore` runbook covering the full
  schema (110 tables) and the Alembic version table, plus the encrypted
  `MICROSOFT_TOKEN_KEY` handling (the key must be backed up separately from the DB,
  or restored tokens are undecryptable — an explicit note).
- **Rehearsal:** a documented restore rehearsal against a scratch database that
  (a) restores a dump, (b) confirms `alembic heads` shows the single expected head,
  (c) runs the full test suite against the restored DB, and (d) verifies sentinel
  row counts. This is a process gate with recorded evidence, not application code.
- **Data-durability fix (candidate):** advisor notes are stored as flat `.txt`
  files outside the DB backup (RC8). Moving them into a `person_notes` table is the
  cleanest durability fix; scoped here as an *optional* ride-along migration if it
  can be done low-risk, otherwise a fast follow.

---

## 15. Testing Strategy

- **Token protection:** unit tests for `encrypt`/`decrypt` round-trip; a stored
  token is ciphertext (not plaintext) in the DB; missing key fails closed.
- **Refresh lifecycle:** with MSAL mocked, `get_microsoft_access_token` returns a
  refreshed token from a persisted cache, re-persists the cache, and raises the
  reconnect error on silent-refresh failure; `offline_access` is in the requested
  scopes.
- **Scope reduction:** assert `DELEGATED_SCOPES` is read-only + `offline_access`.
- **Connector removal:** import-time test that the app starts with the connector
  tree removed; grep-guard test that removed symbols are not referenced.
- **Performance/N+1:** per hot path, a query-count assertion that the count is
  bounded (independent of N) and output is identical to the pre-refactor result;
  new negative-scope tests proving the SQL authorization matches the old Python
  filter (an advisor still sees only their book).
- **Indexing:** migration applies cleanly; `EXPLAIN` (or planner check) confirms
  index use on a representative hot query.
- **Readiness/monitoring:** `/readiness` returns DB + sync-health + head; `/health`
  works without the DB.
- **Platform gates (every release):** full suite, compilation, startup/shutdown,
  route + OpenAPI, template render, clean base→head, v0.9.8 upgrade/downgrade/
  re-upgrade, sentinel preservation, one Alembic head, `git diff --check`.
- **Adversarial regression:** re-run the 0.9.7 authorization and 5.4 H13/RC11
  harnesses to confirm the performance rewrites introduced no authorization or
  cross-client regression.

---

## 16. Migration Impact

- One or more additive, reversible migrations parented at `l2c03f1e0d9b`, single
  head maintained:
  - `microsoft_accounts`: add `token_cache_encrypted`, `last_sync_at`,
    `last_sync_status`, `last_sync_error`; keep plaintext token columns nullable for
    one release (safe rollback), scheduled for removal later.
  - Index migration(s): the H20 indexes via `CREATE INDEX CONCURRENTLY` in an
    `autocommit_block()` — **not** the default transactional block; may be split
    into several small migrations to bound lock/connection risk.
  - Optional `person_notes` table if the notes ride-along is included.
- **Backward compatibility:** existing Microsoft 365 accounts must re-connect once
  (they have no usable refresh token today); all other data is untouched. The
  scope reduction changes the consent screen for new connections only. Downgrade
  drops the new columns/indexes; the connector/dead-code removals are code-only.
- **Production rollout:** index builds must be scheduled with `CONCURRENTLY` and
  monitored; the migration must not `metadata.create_all()`.

---

## 17. Acceptance Criteria

1. Microsoft 365 tokens are **encrypted at rest**; no plaintext token/cache value
   is stored or logged; a missing encryption key fails closed.
2. Token **refresh works**: with `offline_access` and a persisted MSAL cache, sync
   jobs obtain valid tokens beyond the initial ~1 hour without re-connect; a
   genuine re-consent still degrades gracefully to the existing reconnect error.
3. Requested Graph scopes are **read-only + offline_access** (no `Mail.Send` /
   `*.ReadWrite`).
4. The **unused Graph connector modules are removed**; the app starts and all tests
   pass without them; nothing references removed symbols.
5. The **debug `POST /timeline/test` endpoint is removed**; the filing-provider and
   e-signature modules are either removed or explicitly marked reserved for Sprint
   5.6 (reviewer-approved).
6. The four **N+1 hot paths are bounded** (query count independent of N), with
   **identical results and identical authorization scope** proven by tests, and the
   **H20 indexes are added** (CONCURRENTLY-safe) and planner-selected.
7. A **readiness endpoint** reports DB connectivity, Microsoft 365 sync-health, and
   the Alembic head; `/health` works without the DB; sync-health is surfaced on
   `/microsoft365/status`.
8. A **backup/restore runbook** exists and a **restore rehearsal** passes (restored
   DB has one head, green test suite, preserved sentinel counts).
9. Clean install and **v0.9.8 upgrade/downgrade/re-upgrade preserve sentinel data**
   with exactly **one Alembic head**; the full suite (plus the re-run 0.9.7 and 5.4
   adversarial harnesses) is green; `git diff --check` clean.
10. No new product feature is introduced; least privilege, immutable audit, and
    record-level authorization are preserved.

---

*Design submitted for review. No application code has been written and nothing has
been committed for Release 0.9.9. Awaiting approval before implementation.*
