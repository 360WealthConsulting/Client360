# Release 0.9.9 — Platform Consolidation: Implementation Plan

**Status:** implementation checklist — no application code written yet.
**Branch:** `feature/platform-consolidation`.
**Baseline:** Release v0.9.8 (`main` @ `0e530b3`, Alembic head `l2c03f1e0d9b`).
**Governing design:** `docs/PRODUCTION_ARCHITECTURE.md`.
**Release design:** `docs/RELEASE_0.9.9_PLATFORM_CONSOLIDATION.md`.
**Backlog:** `docs/VERSION_1_PROGRESS_REVIEW.md`.

This plan decomposes Release 0.9.9 into eight phases of small, independently
reviewable work packages (WPs). Each WP is intended to be a single focused
commit/PR-sized change with its own tests. Phases are ordered to retire the
highest-risk security debt first and to keep every intermediate state green
(full suite passing, one Alembic head).

**Global rules for every WP.** No new product features. Preserve least privilege,
immutable audit, and record-level authorization. Additive/reversible migrations,
single Alembic head, sentinel preservation. Behavior-preserving unless a change
intentionally alters an authorization/scope result (which must be covered by a new
negative test). `git diff --check` clean. Re-run the 0.9.7 authorization and 5.4
H13/RC11 adversarial harnesses after any change that touches authorization,
scope, or a hot query path.

**Complexity legend:** S = ≤½ day · M = 1–2 days · L = 3–5 days.

---

## Phase 1 — Microsoft 365 Token Security  — **IMPLEMENTED**

> **Implementation note (offline_access).** MSAL **rejects** `offline_access` as an
> explicit scope (it is reserved) and adds it automatically to the auth-code flow,
> which issues a refresh token into the token cache. The delivered fix therefore
> persists the **encrypted MSAL token cache** (holding the refresh token) and
> obtains access tokens via `acquire_token_silent` (transparent refresh) — rather
> than listing `offline_access`. This achieves the objective (durable refresh) and
> is reflected in the acceptance criteria below.

**Objective.** Eliminate plaintext OAuth token storage and implement a real
refresh lifecycle so Microsoft sync survives beyond ~1 hour (RC8/RC9 H10;
architecture §9/§19, ADR-13). Reduce Graph scopes to least privilege.

**Affected files.** New `app/security/token_crypto.py` (Fernet helper); new/edited
`app/services/microsoft_identity.py` (or a helper in `app/jobs/`) for
`get_microsoft_access_token`; `app/routes/microsoft365_oauth.py` (scopes, cache
persistence, no plaintext); `app/jobs/microsoft_mail_sync.py`,
`microsoft_calendar_sync.py`, `microsoft_document_sync.py` (obtain token via the
helper); `app/routes/microsoft365.py` (status surfacing); `app/config.py`
(`MICROSOFT_TOKEN_KEY`, fail-closed); `app/db.py` (new columns); migration.

**Database changes.** `microsoft_accounts`: add `token_cache_encrypted`
(Text/BYTEA, nullable), `last_sync_at`, `last_sync_status`, `last_sync_error`.
Keep `access_token`/`refresh_token` columns nullable (retained one release for
rollback; removal scheduled later).

**Migration impact.** One additive migration parented at `l2c03f1e0d9b`; nullable
columns only — no data rewrite. Existing connected accounts must re-connect once
(no usable refresh token exists today — not a new regression). Downgrade drops the
new columns.

**Work packages.**
- **WP1.1 (S)** — `token_crypto.py`: Fernet `encrypt(bytes)->str` /
  `decrypt(str)->bytes`; reads `MICROSOFT_TOKEN_KEY`; fail-closed if absent; never
  logs plaintext. Unit tests.
- **WP1.2 (S)** — migration: add the four columns; update `app/db.py`; apply/verify
  on a scratch DB.
- **WP1.3 (M)** — `get_microsoft_access_token(account)` helper: load encrypted MSAL
  `SerializableTokenCache`, `acquire_token_silent`, re-persist updated cache,
  return bearer token; raise the existing reconnect `RuntimeError` on refresh
  failure. MSAL-mocked unit tests.
- **WP1.4 (S)** — OAuth callback: add `offline_access`, reduce `DELEGATED_SCOPES`
  to read-only (`User.Read`, `Mail.Read`, `Calendars.Read`, `Files.Read.All`,
  `Sites.Read.All`, `offline_access`); persist the encrypted cache instead of a
  plaintext access token.
- **WP1.5 (M)** — sync jobs: replace direct `account["access_token"]` reads with
  the helper; record `last_sync_at/status/error`.
- **WP1.6 (S)** — surface sync-health on `/microsoft365/status`.

**Testing requirements.** Round-trip encrypt/decrypt; stored value is ciphertext,
not plaintext; missing key fails closed; helper refreshes and re-persists;
`offline_access` present and scopes read-only; sync job records health; jobs
degrade gracefully on refresh failure.

**Validation requirements.** Full suite green; startup/shutdown; migration
up/down/re-up + sentinel; one head. No plaintext token appears in DB or logs.

**Estimated complexity.** L (phase total).

**Dependencies.** None (`cryptography`/`msal` already present). Foundational for
Phase 2.

**Acceptance criteria.** Tokens encrypted at rest; refresh works beyond one hour
in tests; scopes read-only + `offline_access`; sync-health visible; graceful
reconnect on genuine re-consent; existing accounts re-connect once.

---

## Phase 2 — Microsoft Graph Consolidation  — **IMPLEMENTED**

> **Implementation note.** Reference analysis confirmed `auth.py`, `graph.py`,
> `calendar.py`, `mail.py`, `contacts.py`, and `sharepoint.py` formed a closed
> cluster imported only by one another — no live route, job, or service imported
> them. All six were removed; `config.py` (used by the OAuth route, status route,
> and the Phase 1 identity service) and the empty `__init__.py` package marker
> were retained. `git diff --check` clean; 178 routes unchanged; single Alembic
> head `m3d14a2f1e0c` (code-only, no migration). Guard tests added in
> `tests/test_microsoft_graph_consolidation.py` (13 tests).

**Objective.** Consolidate all Graph token acquisition behind the Phase 1 helper
and remove the unused app-only Graph client (architecture §8, ADR-7).

**Affected files.** `app/connectors/microsoft365/` (remove `auth.py`, `graph.py`,
`calendar.py`, `mail.py`, `contacts.py`, `sharepoint.py`; keep `config.py` and a
trimmed `__init__.py`).

**Database changes.** None.

**Migration impact.** None (code-only).

**Work packages.**
- **WP2.1 (S)** — grep-verify nothing outside `connectors/microsoft365/` imports
  the removed modules' symbols; add a guard test asserting no such references.
- **WP2.2 (S)** — delete the six unused modules; trim `__init__.py`; confirm
  `config.get_microsoft365_config` still imported by `microsoft365_oauth.py` /
  `microsoft365.py`.

**Testing requirements.** App imports/starts with the tree removed; no reference to
removed symbols; existing Microsoft tests still pass.

**Validation requirements.** Startup/shutdown; route registration; full suite.

**Estimated complexity.** S.

**Dependencies.** Phase 1 (the single provider path must exist before removing the
alternate client).

**Acceptance criteria.** Only `config.py` remains under `connectors/microsoft365/`;
app starts; all tests pass; nothing references removed symbols.

---

## Phase 3 — Provider Abstraction Cleanup  — **IMPLEMENTED**

> **Implementation note.** Analysis found the confirmed duplication was a second
> copy of the registry pattern: `portal/signatures.py` declared its own
> `SignatureProviderRegistry`, byte-identical to `PortalIdentityProviderRegistry`
> in `portal/providers.py` except the error string. Consolidated both onto one
> canonical, label-parameterized `ProviderRegistry` in `portal/providers.py`
> (`PortalIdentityProviderRegistry` kept as a backwards-compatible alias); error
> messages preserved exactly. `tax_filing_providers.py` is unwired and not a
> duplicate — kept with a reserved-for-Sprint-5.6 docstring (plan default).
> `signatures.py` is test-covered (not dead) — kept, retargeted to the canonical
> registry, docstring added. No auth/token/schema/route change; 178 routes
> unchanged; single head `m3d14a2f1e0c`. Regression tests added in
> `tests/test_provider_abstraction.py` (9 tests) covering identity, signature,
> notification, and tax-filing provider paths; §17 wording refreshed.

**Objective.** Establish a single documented provider-adapter posture and resolve
the status of the two unwired provider modules (architecture §17). Per the release
design, this phase makes an explicit, reviewer-approved decision rather than
silent churn.

**Affected files.** `app/services/tax_filing_providers.py` (docstring or removal);
`app/portal/signatures.py` (docstring or removal); no route/service wiring (that is
Epic 5 Sprint 5.6).

**Database changes.** None.

**Migration impact.** None.

**Work packages.**
- **WP3.1 (S)** — `tax_filing_providers.py`: add a docstring marking it **reserved
  for Epic 5 Sprint 5.6** (filing-provider wiring), OR remove pending reviewer
  decision. Default recommendation: **keep with docstring** (avoids churn since 5.6
  is near-term).
- **WP3.2 (S)** — `portal/signatures.py`: same treatment (reserved for Sprint 5.6
  e-file authorization), OR remove. Default recommendation: **keep with docstring**.
- **WP3.3 (S)** — document the single Graph provider path (Phase 1/2) and the
  notification/OIDC/e-file/e-sign port status in a short note in
  `docs/PRODUCTION_ARCHITECTURE.md` §17 if any wording drifts.

**Testing requirements.** No behavior change; existing tests pass; if a module is
removed, a guard test asserts nothing imports it.

**Validation requirements.** Full suite; startup.

**Estimated complexity.** S.

**Dependencies.** None (independent of Phases 1–2); the keep-vs-remove decision is
the only gate and requires reviewer sign-off.

**Acceptance criteria.** Each unwired provider module is either removed or
explicitly marked reserved for Sprint 5.6; the single Graph provider path is
documented; no dead-but-ambiguous provider code remains.

---

## Phase 4 — Database Optimization (Indexing)  — **IMPLEMENTED**

> **Implementation note.** Two CONCURRENTLY migrations were added:
> `n4e25b3c2f1d` (batch 1, 14 hot-path indexes) and `o5f36c4d3e2a` (batch 2, 10
> remaining query-justified scope columns). Batch 2 was scoped by actual query
> predicates rather than the full ~150 unindexed FK columns — pure audit
> back-references are intentionally not indexed. `tax_engagement_return_id`,
> `workflow_steps(workflow_instance_id)`, and `portal_access_grants(portal_account_id)`
> were found already indexed and excluded. All 24 indexes verified valid and
> planner-selected; measured ~2.7× on a 63k-row `timeline_events` person lookup.
> Migration up/down/re-up reversible with sentinel preservation from v0.9.8;
> single head `o5f36c4d3e2a`; 167 tests pass; 178 routes; OpenAPI intact. Details
> in `docs/RELEASE_0.9.9_PHASE4_INDEXES.md`; regression test in
> `tests/test_index_optimization.py`.

**Objective.** Add the missing foreign-key / hot-column indexes (RC9 H20;
architecture §5/§23) so per-request cost is index-bound. Indexing precedes the
Phase 5 query rewrites so the rewrites are validated against indexed tables.

**Affected files.** One or more index migrations; `app/db.py` unaffected (indexes
are not reflected as attributes needing export).

**Database changes.** New indexes (batch 1, hot path): `people.household_id`,
`tasks.person_id`, `activities.person_id`, `documents.person_id`,
`timeline_events.person_id`, `timeline_events.household_id`,
`household_relationships.person_id`, `portal_notifications.portal_account_id`,
`portal_threads.household_id`, `portal_threads.person_id`,
`tax_engagements.person_id`, `tax_engagements.household_id`,
`audit_events.actor_user_id`, `audit_events(entity_type, entity_id)`. Batch 2:
remaining FK columns from the RC9 list.

**Migration impact.** Built with `CREATE INDEX CONCURRENTLY` inside an Alembic
`op.get_context().autocommit_block()` (the default transactional block cannot);
split into small migrations to bound lock/connection risk; single head maintained;
downgrade drops each index. No data change.

**Work packages.**
- **WP4.1 (M)** — batch-1 hot-path indexes migration (CONCURRENTLY /
  autocommit_block); apply and verify on a scratch DB.
- **WP4.2 (M)** — batch-2 remaining FK indexes migration.
- **WP4.3 (S)** — planner verification: `EXPLAIN` a representative hot query
  (e.g. per-client `client_summary` join) confirms index use.

**Testing requirements.** Migration applies/reverts cleanly; a planner check shows
the new index selected; no query result changes.

**Validation requirements.** Base→head; v0.9.8 up/down/re-up + sentinel; one head.
Document that production builds use CONCURRENTLY and are monitored.

**Estimated complexity.** M.

**Dependencies.** None; should land before Phase 5 (rewrites depend on the indexes
for their performance assertions).

**Acceptance criteria.** All batch-1 indexes present and planner-selected;
migrations CONCURRENTLY-safe and reversible; one head; sentinel preserved.

---

## Phase 5 — Performance Improvements (N+1 Elimination)  — **IMPLEMENTED**

> **Implementation note.** All five WPs delivered with identical-output and
> query-count-independent-of-N tests (`tests/test_phase5_query_optimization.py`):
> WP5.1 pushes `work_items()` authorization into SQL (O(book), negative-scope
> tested); WP5.2 bulk `_bulk_intake_details` (4 returns 28→7 queries, output ==
> `intake_detail`); WP5.3 threads one `portal_scope()` and gives narrow endpoints
> dedicated functions (`/notifications` 21→1 query); WP5.4 bulk concentration via
> the same `aggregate_portfolio` math (4 people 28→2 queries, numerically
> identical); WP5.5 pagination on `/activities` and `/tasks`. No schema change;
> head `o5f36c4d3e2a`; 178 tests pass; 178 routes; OpenAPI intact. Details in
> `docs/RELEASE_0.9.9_PHASE5_QUERY_OPTIMIZATION.md`.

**Objective.** Eliminate the four confirmed N+1 / full-scan hot paths (RC8/RC9
H15–H19; architecture §23) while **preserving exact authorization/scope
semantics**. Each rewrite is a separate WP with a query-count assertion and a
negative-scope test.

**Affected files.** `app/services/work_management.py` (`work_items`);
`app/services/tax_intake.py` (`staff_dashboard`), `app/routes/tax_intake.py`
(`api_detail`); `app/portal/service.py` (`dashboard` scope threading),
`app/routes/portal.py` (narrow endpoints); `app/services/portfolio.py`
(`search_portfolios`); `app/routes/activity_dashboard.py`,
`app/routes/task_dashboard.py`, `app/services/relationships.py` (pagination).

**Database changes.** None (relies on Phase 4 indexes).

**Migration impact.** None.

**Work packages.**
- **WP5.1 (L)** — `work_items()`: push the authorization `EXISTS` predicate and the
  priority/status/team/due filters into SQL (mirror `production_dashboard()`).
  Query-count bounded (independent of N); negative-scope test (advisor sees only
  their book); output identical to the pre-refactor result on a fixture set.
- **WP5.2 (M)** — tax intake `staff_dashboard()`: bulk `WHERE … IN (return_ids)`
  queries instead of the per-return loop; `api_detail()` authorizes via a cheap
  id-set check. Query-count + identical-output tests.
- **WP5.3 (M)** — portal `dashboard()`: thread one `portal_scope()` through
  sub-calls; give narrow endpoints (`/documents`, `/requests`, `/tasks`,
  `/notifications`) dedicated single-purpose queries. Query-count + identical-output
  tests; portal isolation re-verified.
- **WP5.4 (M)** — `search_portfolios()` concentration: compute the concentration
  ratio in SQL (window/subquery); add `LIMIT`. Cross-validate the numeric result
  against the existing Python calculation.
- **WP5.5 (S)** — pagination: `limit`/`offset` (sane defaults) on `/activities`,
  `/tasks`, and the relationship/portfolio search services; update templates/count
  logic that assumed "all rows".

**Testing requirements.** Per path: a bounded query-count assertion; identical
result vs pre-refactor on fixtures; a negative-scope test proving the SQL
authorization equals the old Python filter. Re-run the 0.7 authorization harness.

**Validation requirements.** Full suite; adversarial harness re-run (no authz /
cross-client regression); startup; templates render.

**Estimated complexity.** L (phase total).

**Dependencies.** Phase 4 (indexes). WP5.3 benefits from Phase 1 only indirectly.

**Acceptance criteria.** Each hot path is O(caller's book) with a bounded
query-count test, identical results, and identical authorization scope proven by
tests; pagination added; no authorization regression.

---

## Phase 6 — Dead Code Removal

**Objective.** Remove residual dead/debug code (architecture §11/§4; progress
review §11) beyond the Graph connector (Phase 2).

**Affected files.** `app/routes/timeline.py` (`POST /timeline/test`);
`app/security/policy.py`, `app/security/service.py` (unused imports); any other
RC8-flagged unused import.

**Database changes.** None.

**Migration impact.** None.

**Work packages.**
- **WP6.1 (S)** — remove the `POST /timeline/test` debug endpoint (writes a
  synthetic event into `person_id=1`); confirm no test depends on it.
- **WP6.2 (S)** — remove unused imports flagged in `policy.py` / `service.py`;
  compile clean.

**Testing requirements.** Route no longer registered; full suite passes;
compilation clean.

**Validation requirements.** Route registration diff (endpoint gone); OpenAPI
regenerates; full suite.

**Estimated complexity.** S.

**Dependencies.** None (independent). Do after Phase 2 to batch removals.

**Acceptance criteria.** Debug endpoint and unused imports removed; no test or
route references them; suite green.

---

## Phase 7 — Deployment Readiness

**Objective.** Add operational scaffolding: a readiness endpoint, config/secret
hardening, CSRF defense-in-depth, and documented backup/restore (architecture
§20/§21/§22; release design §12–14).

**Affected files.** New `/readiness` route (extend `app/routes/dashboard.py` or a
new `app/routes/ops.py`); `app/config.py` (session-secret warning); optional
`app/security/middleware.py` (CSRF Referer fallback); docs (backup/restore
runbook). Optional advisor-notes: new `person_notes` table + `app/services/notes.py`
migration off flat files.

**Database changes.** Optional `person_notes` table (if the advisor-notes
durability fix rides along). Otherwise none.

**Migration impact.** If `person_notes` is included: one additive migration + a
resumable, dry-run-first backfill from the flat files; downgrade drops the table
(files retained). Otherwise none.

**Work packages.**
- **WP7.1 (M)** — `/readiness`: DB connectivity check, Microsoft sync-health
  summary, and current Alembic head; keep `/health` DB-independent (liveness).
- **WP7.2 (S)** — config hardening: loud startup warning when `SESSION_SECRET`
  uses the dev fallback; document required env vars in one place.
- **WP7.3 (S)** — CSRF defense-in-depth: add a `Referer` fallback to the Origin
  check (do not weaken existing behavior).
- **WP7.4 (S)** — backup/restore runbook + rehearsal script (restore → one head →
  green suite → sentinel counts), including `MICROSOFT_TOKEN_KEY` handling (backed
  up separately). Documentation + a scratch-DB rehearsal, not production code.
- **WP7.5 (M, optional/reviewer-gated)** — `person_notes` table + migrate advisor
  notes off flat files (resumable, dry-run counts, idempotent); update
  `app/services/notes.py` readers/writers.

**Testing requirements.** `/readiness` returns DB + sync-health + head; `/health`
works without DB; session-secret warning fires on fallback; CSRF still rejects
cross-origin. If notes migrated: read/write parity + backfill idempotency tests.

**Validation requirements.** Startup/shutdown; route + OpenAPI; restore rehearsal
recorded; full suite.

**Estimated complexity.** M (L if WP7.5 included).

**Dependencies.** Phase 1 (sync-health columns feed the readiness endpoint).

**Acceptance criteria.** Readiness endpoint reports DB + sync-health + head;
`/health` DB-independent; secret hardening + CSRF fallback in place; backup/restore
runbook exists and a rehearsal passes; if included, advisor notes are in the DB and
backed up.

---

## Phase 8 — Validation and Release

**Objective.** Full-platform validation, documentation finalization, draft PR, and
completion report (release design §15–17). No merge without an independent RC pass.

**Affected files.** `CHANGELOG.md`, `README.md`, `docs/ROADMAP.md`,
`docs/RELEASE_0.9.9_PLATFORM_CONSOLIDATION.md` (mark implemented),
`docs/PRODUCTION_ARCHITECTURE.md` (flip relevant `[Planned: 0.9.9]` → `[Implemented]`),
new `docs/RELEASE_0.9.9.md`.

**Database changes.** None (documentation and validation only).

**Migration impact.** Confirm exactly one Alembic head across all phase migrations;
full up/down/re-up.

**Work packages.**
- **WP8.1 (M)** — full validation sweep: suite, compile, startup/shutdown, route +
  OpenAPI, templates, clean base→head, v0.9.8 upgrade/downgrade/re-upgrade,
  sentinel preservation, append-only audit, git whitespace.
- **WP8.2 (M)** — adversarial re-validation: re-run the 0.9.7 authorization harness
  and the 5.4 H13/RC11 harnesses; confirm no authorization or cross-client
  regression from the performance rewrites; confirm no plaintext token in DB/logs.
- **WP8.3 (S)** — documentation: mark 0.9.9 items implemented across the release
  design and architecture doc; write `docs/RELEASE_0.9.9.md`; update
  CHANGELOG/README/ROADMAP.
- **WP8.4 (S)** — commit, push `feature/platform-consolidation`, open a **draft** PR
  to `main`, produce the standard completion report. **Do not merge.**

**Testing requirements.** All gates green; adversarial harnesses green; no
plaintext-token exposure.

**Validation requirements.** Independent RC validation (RC12) is required before
merge (analogous to prior releases); this phase produces the draft PR only.

**Estimated complexity.** M.

**Dependencies.** All prior phases.

**Acceptance criteria.** Full suite + adversarial harnesses green; one Alembic head;
sentinel preserved; documentation finalized; draft PR opened; completion report
produced; not merged.

---

## Phase Dependency Overview

```
P1 Token security ──► P2 Graph consolidation
P1 ─────────────────► P7 Deployment readiness (sync-health -> /readiness)
P3 Provider cleanup (independent; reviewer-gated decision)
P4 Indexing ────────► P5 Performance (rewrites validated on indexed tables)
P6 Dead code (independent)
P1..P7 ─────────────► P8 Validation & draft PR
```

Recommended execution order: **P1 → P2 → P4 → P5 → P3 → P6 → P7 → P8** (security
first; indexes before rewrites; cleanup and readiness before the final validation).

## Cross-Phase Acceptance (release-level)

- Microsoft 365 tokens encrypted at rest with a working refresh lifecycle;
  read-only scopes; sync-health visible.
- Single Graph provider path; unused connector removed; dead code removed;
  unwired provider modules resolved (removed or reserved-for-5.6).
- Missing hot-path indexes added; the four N+1 hot paths bounded with identical
  results and identical authorization scope.
- Readiness endpoint + config/CSRF hardening + backup/restore rehearsal.
- Exactly one Alembic head; v0.9.8 upgrade/downgrade/re-upgrade preserves sentinel
  data; full suite and adversarial harnesses green; draft PR opened, not merged.

---

*Implementation plan only. No application code has been written and nothing has
been committed on `feature/platform-consolidation`. Awaiting approval before
beginning implementation.*
