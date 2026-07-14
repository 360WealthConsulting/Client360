# Client360 — RC8 Architecture Review

**Scope:** comprehensive, read-only architectural review of every major subsystem shipped through Release v0.9.6 (Alembic head `i970d9f7b8c9`), performed prior to beginning Sprint 5.4.
**Method:** eight independent deep-dive research passes (Auth/Security/Audit; Microsoft 365 integration; Client CRM + Relationships + Households + Documents + Timeline + Activities + Search + Dashboards + Portfolio; Work Management + Workflow Engine; Client Portal; Tax Domain + Tax Engagement Intake + Tax Return Lifecycle; Database schema + migrations; API design + naming consistency across all routers), each reading the relevant source in full, cross-referencing migrations, and citing concrete `file:line` evidence.
**Non-goal:** no application code was modified. No fixes were implemented. This document is an assessment and a prioritized backlog only.

---

## Executive Summary

Client360 has shipped nine sprints of substantial functionality — a full practice-management platform (work/workflow engine), a client portal with its own identity system, Microsoft 365 integration, relationship/portfolio intelligence, and now three consecutive sprints of a tax practice platform (domain → intake → return lifecycle) — on a single linear migration history with a real, if uneven, commitment to reusable platform primitives (capability-based RBAC, immutable audit events, record-level scoping, idempotent event ledgers, versioned/snapshotted workflow templates).

That reuse discipline is real but was not applied uniformly, and it was not retrofitted onto earlier code as later sprints improved the pattern. The review surfaced two categories of finding that matter for a pre-1.0 sign-off:

1. **Concrete, exploitable authorization bugs** — not theoretical: a `work.write` holder can self-grant read/write access to any client record; a `role.manage` holder can self-escalate to any capability including `administrator`; two tax-return endpoints skip the record-scope check every sibling endpoint in the same file applies; a client portal delegate whose `messages` permission is explicitly set to `False` can still send and read secure messages; an advisor can sever another advisor's client's relationship edge with no ownership check. These were each found independently by different review passes and are consistent with each other — they are real gaps in the same underlying pattern (see "Weaknesses" below), not one-off typos.
2. **Systemic consistency debt** — three independent, semantically-diverging implementations of "is this record assigned to me"; a coarse regex-driven authorization middleware that was never co-designed with the fine-grained per-route capability checks it sits in front of (already causing one confirmed functional lockout — compliance reviewers — and enabling one of the exploitable bugs above); ~48 missing indexes on hot foreign-key columns; ~16 missing CHECK/UNIQUE/FK constraints; a database schema roughly half of which has no Python model at all (migrations are the only source of truth); N+1 query patterns in at least five dashboards; and an API surface where every router invents its own response envelope and its own exception→HTTP-status mapping.

None of this reflects an unsound architecture — the core primitives (capability RBAC, append-only audit, workflow snapshotting, portal grant scoping) are genuinely well designed, and several subsystems (workflow template versioning, portal token handling, the newer tax-domain CHECK constraints) show real security and engineering maturity. What's missing is a consolidation pass: the codebase has accumulated the normal cost of nine fast sprints without a periodic "go back and unify" cycle, and that cost has now compounded into real security exposure, not just cosmetic inconsistency. This should be treated as a required hardening milestone before Release 1.0, not optional cleanup.

---

## Overall Architecture Score: **6 / 10**

*"Functionally broad and architecturally sound in its core primitives, but not yet production-hardened — several concrete, exploitable authorization bugs and systemic consistency gaps must be closed before Release 1.0."*

| Subsystem | Score /10 | One-line rationale |
|---|---|---|
| Authentication core (OIDC, MFA, session tokens) | 7 | Well-built primitives; hardcoded dev secret fallback and CSRF fail-open are the main gaps |
| Authorization (RBAC/capabilities/record-scope) | 4 | Strong concept, undermined by two live privilege-escalation paths and three divergent scope implementations |
| Audit | 6 | Solid append-only/trigger design; coverage gaps (reads, approvals, corrections not logged) |
| Microsoft 365 integration | 4 | Broad feature surface; no token refresh, plaintext token storage, single-global-account ceiling, ~600 lines of dead parallel implementation |
| Client CRM (people/households/relationships/documents/timeline/activities/search) | 6 | Good centralized scoping model; missing indexes on every hot table, two IDOR gaps, dead dashboard metric |
| Work Management & Workflow Engine | 6 | Excellent template-versioning/cycle-detection design; unfiltered full-table dashboard scans, half-wired escalation/automation subsystems |
| Client Portal | 5 | Sound grant-scoping model reused well by tax subsystems; no functioning login/password-reset, unenforced `messages` permission, N+1 on every page |
| Tax Domain / Intake / Return Lifecycle | 6 | Rich functionality, good reuse of platform primitives at the macro level; real cross-sprint drift (dead metric, household-context divergence, orphaned filing-provider abstraction) |
| Database schema & migrations | 5 | ~48 missing indexes, ~16 missing constraints, `app/db.py`/`app/database/schema.py` split leaves half the schema undocumented in Python |
| API design & naming consistency | 5 | Bimodal: Epic 5 routers reasonably disciplined; legacy CRM routers have no versioning, no in-router auth, no shared templates |
| Performance / scalability | 4 | N+1 patterns in ≥5 dashboards, no pagination story anywhere, several full-table scans on every request |
| Technical debt | 4 | Dead/orphaned modules (filing providers, portal signatures, MS365 connector duplicate), two authorization layers to keep in sync by hand, undocumented schema |

---

## Strengths

- **Capability-based RBAC with record-level scoping** (`app/security/policy.py`, `app/security/dependencies.py`) is a coherent, well-thought-out core model — `record_assignments` + capability composition is the right shape for a multi-advisor practice.
- **Append-only, DB-trigger-enforced audit and event ledgers** — `audit_events`, `workflow_events`, `tax_return_lifecycle_events`, `tax_filing_events`, `portal_messages`, `assignment_events` are all protected against mutation at the database layer, not just in application code. This is real defense-in-depth.
- **Idempotency-key discipline** is consistently applied wherever it matters: `workflow_instances.idempotency_key`, `workflow_events.idempotency_key`, `automation_actions.idempotency_key`, `portal_notifications.idempotency_key`, `tax_filing_events.idempotency_key` are all unique-constrained and checked before insert.
- **Workflow template versioning and immutability** (`app/services/workflow_automation.py`, DB triggers in `e530f5b3d4e5`) — snapshot-on-launch, published-template freeze, and step-dependency cycle detection are all correctly implemented and enforced at the database layer, not just in application code.
- **Portal grant/scope/notify model is genuinely reused, not reinvented**, by the two most recent subsystems (`tax_intake.py`, `tax_return_lifecycle.py` both call `portal_scope`/`require_scope`/`notify` directly rather than rolling their own). This is the platform's best example of the reuse discipline working as intended.
- **Session and credential handling fundamentals are sound**: OIDC-only staff login (no home-grown password storage), passwordless MFA-gated portal auth, SHA-256-hashed opaque session tokens, `secrets.compare_digest` for OIDC state comparison, `secrets.token_urlsafe(32)` for portal invitation/reset tokens with single-use row-locked redemption.
- **Newer tax-domain migrations show real constraint discipline** (`ck_tax_year_range`, `ck_tax_engagement_subject`, dedicated lookup tables for return types/filing statuses/jurisdictions) that, if retrofitted onto the older schema, would resolve a large share of the normalization findings below.
- **Delta-aware, paginated Microsoft Graph document sync** (`app/jobs/microsoft_document_sync.py`) correctly implements `@odata.nextLink` following and persisted `@odata.deltaLink` cursors — the one part of the M365 integration built to handle real-world volume.
- Documentation quality is consistently good — every sprint has a companion architecture doc (`docs/TAX_RETURN_LIFECYCLE.md`, `docs/TAX_ENGAGEMENT_INTAKE.md`, etc.) that mostly reflects what actually shipped, and known limitations are proactively documented rather than hidden.

---

## Weaknesses

- **Authorization enforcement is split across two layers that were never co-designed**: a coarse `RULES` regex table in `app/security/middleware.py` that infers a capability from the URL path, and per-route `require_capability(...)` dependencies. A request must satisfy both. Because these were built independently, mismatches are inevitable and have already produced (a) a confirmed functional lockout (compliance-role reviewers cannot use the `work.approve` capability that was purpose-built for them, because the middleware demands the coarser `work.write`), and (b) contributed to at least one exploitable gap. This was independently discovered by two separate review passes (Auth/Security and API Design), which is a strong signal it's real.
- **Record-level scoping (`record_assignments` → "am I authorized for this record?") is implemented three separate times** with diverging semantics: `has_record_scope()` (checks both `effective_date` and `inactive_date`), `_scope_filter()` in the tax domain (checks only `inactive_date`, also ORs in office membership), and `authorized_assignments()` in work management (matches `has_record_scope`'s dates but adds inline team logic). Three teams' worth of copy-pasted-and-drifted scope logic is a durable bug generator, not a one-time cost.
- **Two live privilege-escalation paths and multiple IDOR gaps exist today**, all rooted in the same failure mode: a write endpoint trusts a capability check but skips the record/entity-ownership check that its sibling endpoints apply. See High Priority Issues below for the full list.
- **Dead and orphaned code sits alongside — and looks like — the real implementation** in at least three places: the provider-neutral tax filing abstraction (`app/services/tax_filing_providers.py`, never imported), the portal e-signature module (`app/portal/signatures.py`, never routed), and a fully-built alternate Microsoft Graph client (`app/connectors/microsoft365/*`, ~600 lines, architecturally incompatible with the actually-wired implementation). Each is a trap for a future engineer who discovers it and assumes it's live.
- **N+1 and unbounded-query patterns recur across at least five separate dashboards** written by different sprints: `work_items()` (work management), `staff_dashboard()`/`api_detail()` (tax intake), `portal.dashboard()` fan-out (client portal), `search_portfolios()` with concentration filter, and unbounded firm-wide loads in `activity_dashboard.py`/`task_dashboard.py`. Notably, `production_dashboard()` (tax return lifecycle, the *newest* code) correctly uses a single bulk query — proving the team knows the right pattern — but it was never back-ported to the dashboards written one sprint earlier.
- **Roughly half the database schema has no Python `Table` model** — `app/db.py` reflects the live database at import time and is the de facto source of truth for ~90 tables; `app/database/schema.py` and friends define only a subset. This means the app cannot boot without a live, fully-migrated database, and no single file lets a reviewer see the whole schema.
- **~48 missing indexes on foreign-key/hot-filter columns and ~16 missing constraints** (CHECK, UNIQUE, FK) are spread across nearly every subsystem — Postgres does not auto-index FK columns, and that fact was consistently forgotten on the older 60% of the schema even as newer tax-domain tables show better discipline.
- **API response shape, pagination, and error-handling conventions differ per router**, with no shared response model anywhere (zero `response_model` declarations across ~30 route files) — a generic frontend client cannot be written against this API surface without a per-endpoint adapter.
- **OAuth tokens are stored in plaintext** with no application-level encryption, no vault/KMS integration anywhere in the codebase, and broader delegated scopes requested than any wired code path actually uses.

---

## High Priority Issues

Each item below was independently verified with concrete `file:line` evidence during the review. Items marked **(cross-confirmed)** were found by more than one independent research pass.

### Security / Authorization

| # | Issue | Location | Impact |
|---|---|---|---|
| H1 | `POST /api/v1/work/assignments` lets any `work.write` holder self-grant (or grant to anyone) read/write `record_assignments` access to any person/household, fully bypassing the intended `assignment.manage`-gated path | `app/routes/work.py:62-77,121-125`, `app/services/work_management.py:16,57-85` | Privilege escalation — any advisor/operations user can grant themselves access to any client's record |
| H2 | `PUT /admin/roles/{id}/capabilities` and `POST /admin/user-roles` let any `role.manage` holder compose/assign a role with **any** capability set, including `identity.manage`/`role.manage`/`administrator`, with no subset check against the actor's own grants | `app/routes/admin.py:42-48`, `app/services/identity.py:27-34` | Privilege escalation — self-service path to full administrator |
| H3 | Two tax-return mutating endpoints skip the `_authorized()` record-scope check every sibling endpoint in the same file applies | `app/routes/tax_returns.py:52-59` (`api_review_decision`, `api_resolve`), `app/services/tax_return_lifecycle.py:73-90` | IDOR — reviewers/correctors can act on returns outside their office/assignment scope |
| H4 | Coarse middleware capability inference (`RULES` regex table) and fine-grained route-level `require_capability()` were never co-designed **(cross-confirmed: Auth review + API review)** | `app/security/middleware.py:16-42,112-115`, `app/routes/workflows.py:69`, `app/routes/tax_returns.py:53` | Confirmed lockout of compliance approvers today; will silently break any future narrowly-scoped role (e.g. a `tax.review`-only reviewer role) |
| H5 | `POST /relationships/{id}/end` never checks that the relationship belongs to the `person_id` used for the authorization check | `app/routes/relationships.py:52-59`, `app/services/relationships.py:176-223` | IDOR — an advisor can sever a relationship belonging to a client outside their book |
| H6 | "Available people" pickers on person/household profile pages return **all active people firm-wide** with no scope filter, reachable by anyone who can view one client record | `app/routes/people.py:335-339`, `app/routes/households.py:147-167` | Firm-wide client-name/email enumeration, bypassing `record_assignments` scoping |
| H7 | Portal `send_message`/`list_messages`/`mark_read` never check the caller's `messages` permission grant — only household/person scope | `app/portal/service.py:113-124,137-142,144-152` | A delegate explicitly granted `messages: false` can still read/send secure messages on any thread in their household scope |
| H8 | `/api/v1/work/assignments/{id}` reassign/deactivate endpoints check capability but not entity ownership | `app/routes/work.py:121-146`, `app/services/work_management.py:88-121` | IDOR — any `work.write` holder can reassign/deactivate any assignment firm-wide |
| H9 | `process_reminders()` batch job has no office/assignment scope filter, unlike every other read/write path in the tax subsystem | `app/services/tax_intake.py:184-205`, `app/routes/tax_intake.py:63-64` | Office-scoped staff can trigger firm-wide client-facing reminder notifications outside their scope |
| H10 | Microsoft OAuth access/refresh tokens stored in plaintext **(cross-confirmed: M365 review + DB schema review)**; no refresh implementation exists (`refresh_token` hardcoded `None` on save) | `app/database/schema.py:660-661`, `app/routes/microsoft365_oauth.py:190` | DB read access (backup leak, insider access) yields live mailbox/calendar/drive credentials; integration silently stops working ~1 hour after every connect |

### Correctness / Data Integrity

| # | Issue | Location | Impact |
|---|---|---|---|
| H11 | `tax_engagement_returns.status` server default was never migrated off `"not_started"` in Sprint 5.3, and `"not_started"` isn't in the new `STATES` set — the `/tax` dashboard's "Unassigned" metric has silently read 0 since Sprint 5.3 shipped | `migrations/versions/g750b7d5f6a7...py:32`, `migrations/versions/i970d9f7b8c9...py:26`, `app/services/tax_domain.py:72`, `app/services/tax_return_lifecycle.py:14` | A production backlog-visibility KPI is dead and shows a false "0" with no test coverage catching it |
| H12 | Tax intake and tax return lifecycle context-loaders resolve `household_id` differently — intake coalesces from the person's household, lifecycle does not | `app/services/tax_intake.py:18-26` vs `app/services/tax_return_lifecycle.py:25-28` | Household-shared portal accounts can see a return in the portal but get an unexplained 403 acting on it; timeline events for the same return end up filed under different households depending on which subsystem wrote them |
| H13 | Document auto-matching uses unguarded substring containment on email/folder-name heuristics | `app/jobs/microsoft_document_sync.py:79-98` | Real risk of a tax/financial document being auto-assigned to the wrong client in a wealth-management context |
| H14 | Dashboard "pending matches" metric counts a `decision` value (`"pending"`) that is never actually persisted to the table it's queried from | `app/services/dashboard.py:44-48`, `app/routes/matches.py:381-399,552-555` | Homepage KPI always reads 0, misrepresenting the real match-review backlog |

### Scalability

| # | Issue | Location | Impact |
|---|---|---|---|
| H15 | `work_items()` performs two fully unfiltered table loads (`tasks`, `workflow_steps` joined) on every dashboard/queue request, filtering by authorization only after the full scan | `app/services/work_management.py:146-169` | Every "My Work"/"Team Work"/queue page load is O(total system work), not O(caller's book of business) |
| H16 | `staff_dashboard()` (tax intake) and its `api_detail()` caller issue on the order of `1 + 6N` queries for N authorized returns, and `api_detail` additionally recomputes the entire dashboard just to authorize one record | `app/services/tax_intake.py:159-165`, `app/routes/tax_intake.py:35-39` | Slowest page in the tax subsystem; will degrade sharply as engagement volume grows |
| H17 | Portal `dashboard()` fan-out recomputes `portal_scope()` four times and calls per-return detail loaders (4-6 queries each) in an uncached Python loop, and every narrow portal API endpoint (`/documents`, `/requests`, `/tasks`, `/notifications`) pays this full cost just to extract one field | `app/portal/service.py:210-223`, `app/services/tax_return_lifecycle.py:159-162`, `app/services/tax_intake.py:167-172` | A client with 5 tax-year returns triggers ~25-30+ queries on every single portal page/API call |
| H18 | Multiple firm-wide list endpoints load unbounded result sets with no `LIMIT`/pagination | `app/routes/activity_dashboard.py:14-28`, `app/routes/task_dashboard.py:13-28`, `app/services/relationships.py:371-444`, `app/services/portfolio.py:37-47` | The two most-visited daily dashboards are the most exposed to unbounded firm growth |
| H19 | Portfolio concentration search does an N+1 loop calling `get_person_portfolio()` (2-4 queries each) per result row | `app/services/portfolio.py:45-47` | Hundreds of sequential DB round trips per firm-wide concentration search |

### Database

| H20 | ~48 missing indexes on foreign-key/hot-filter columns spanning nearly every subsystem (see Technical Debt Register for the itemized list; highest-impact: `people.household_id`, `tasks.person_id`, `activities.person_id`, `documents.person_id`, `timeline_events.person_id`/`household_id`, `household_relationships.person_id`, `portal_notifications.portal_account_id`, `portal_threads.household_id`/`person_id`, `tax_engagements.person_id`/`household_id`, `tax_missing_items.tax_engagement_return_id`, `audit_events.actor_user_id`/`(entity_type,entity_id)`) | Across `app/database/*.py` and all 19 migrations | The highest-traffic pages in the app (person workspace, portal dashboard, audit search) will degrade to sequential scans as data grows |
| H21 | ~16 missing constraints — no CHECK/lookup-table enforcement on the majority of free-text status/type columns firm-wide, and no FK-style validation on polymorphic `entity_type` values used across 7+ tables | Across `app/database/schema.py`, `identity_tables.py`, `work_tables.py`, `portfolio_tables.py` | Silent data drift risk (typo'd status values become invisible to every exact-match filter); newer tax-domain tables show the fix is already known internally, just not retrofitted |
| H22 | ~50% of tables exist only as raw migration DDL with no corresponding Python `Table` model; `app/db.py` (live reflection) and `app/database/schema.py` (partial static models) are two independent, duplicate-connection-pool sources of schema truth | `app/db.py:14-127`, `app/database/schema.py`, all `f640a6c4e5f6`/`g750b7d5f6a7`/`h860c8e6a7b8`/`i970d9f7b8c9`/`e530f5b3d4e5` migrations | No single file documents the full schema; app cannot start without a live migrated DB; double connection pools per process |

---

## Medium Priority Issues

*(Grouped by theme; full detail and citations are in each subsystem's underlying research — available on request. This section lists the consolidated set warranting a Sprint before 1.0.)*

**Authorization & Audit**
- CSRF `Origin` check fails open when the header is absent (`app/security/middleware.py:72-81`).
- Session secret silently defaults to a hardcoded dev value outside `production` env, also dropping the `Secure` cookie flag (`app/config.py:1-8`).
- Audit metadata redaction is shallow (top-level keys only, substring match, no recursion) (`app/security/redaction.py`).
- No audit trail for reads of sensitive PII/tax data — only mutations and document access are audited.
- Approval decisions, review corrections, and workflow-step completion are never written to `audit_events` (only to the separate `workflow_events` table).
- `work_queues.required_capability` column exists but is never enforced — any `work.read` holder can view the compliance queue.
- Team-routed workflow approvals have no membership check at decision time.

**Microsoft 365**
- No rate-limit/throttling/backoff handling anywhere in the Graph integration — a single 429 aborts an entire sync cycle.
- Mail and calendar sync are single-page, non-paginated fetches (only document sync follows delta links correctly) — silent truncation under real volume.
- Single "most recently connected" account is used for all sync regardless of which advisor connected it — the schema supports multiple accounts but the code doesn't use that support.
- No sync-health observability — failures are logged and discarded, never surfaced in the UI.
- Requested OAuth scopes (`Mail.Send`, `Contacts.ReadWrite`, `Files.ReadWrite`) exceed what any wired code path uses.

**Client CRM / Portfolio**
- Filesystem-only advisor notes (`.txt` files) bypass the audit/authorization/backup model entirely (`app/services/notes.py`).
- `/timeline/person/{id}` breaks the `/people/{id}/{subresource}` convention and, as a result, isn't covered by the middleware's record-path regex — reachable by any `client.read` holder regardless of record scope.
- Leftover test/debug endpoint (`POST /timeline/test`) hardcodes `person_id=1` and writes synthetic events into a real record.
- Full CSV re-parse and re-hash on every match-review page load, with positional (not persisted) group IDs.
- Firm AUM figures don't exclude closed accounts.
- Two incompatible relationship-type models (`household_relationships.relationship_type` free-text vs. `relationships.relationship_type_id` FK) for conceptually the same idea.
- No partial-unique constraint enforcing "one primary household per person" — a real TOCTOU race exists in the application-level unset-then-set logic.

**Work Management / Workflow Engine**
- SLA escalation only scans `workflow_steps`; `tasks.sla_due_at` (and its dedicated index) is dead — plain tasks never get escalated.
- Escalations are created but never consumed — no route/service surfaces them for reassignment or resolution; it's a write-only audit trail.
- The automation-action execution engine (`execute_automation_action`, `automation_status`) is fully unwired — only called from tests.
- Tax return lifecycle bypasses the shared `request_approval()`/`decide_approval()` engine and hand-rolls its own, with different exception semantics for the same segregation-of-duties rule.
- Two disconnected task-assignment systems (`tasks.assigned_to` free-text vs. `record_assignments`) — tasks assigned only via the legacy field are invisible to "My Work."
- SLA scheduler has no leader election — every worker/replica in a multi-process deployment runs its own copy every 5 minutes, causing duplicate-escalation races.

**Client Portal**
- No returning-user login endpoint exists — only invitation acceptance creates a session; the login page is a documented stub.
- Password reset generates and hashes a token but never delivers it to the user (route discards the return value; no notification channel invoked).
- `app/portal/signatures.py` (e-signature) is fully dead code — no route ever calls it, despite e-signature being a stated in-scope capability.
- Portal clients have no way to download/view an actual document file — only metadata is exposed; the real download route lives outside `/portal*` and is inaccessible to portal sessions.
- `document_versions.previous_document_id` is modeled but never populated — no real version-chain lineage.
- Staff cannot originate a new secure-message thread to a client — schema supports it, service layer doesn't expose it.

**Tax Domain / Intake / Lifecycle**
- Filing-provider abstraction (`app/services/tax_filing_providers.py`) is fully orphaned — `record_filing()` never calls into it.
- Authorization-by-listing pattern (`_authorized`/`_authorize_return`) re-runs the full scoped join query to check membership of one ID, on every single-record request, instead of a cheap existence check.
- `ValueError("... not found")` is mapped inconsistently to HTTP 400 vs. 404 across (and within) the tax routers.
- Tax templates (`return_dashboard.html` and siblings) reference 8 CSS classes (`page-header`, `stats-grid`, `stat-card`, `panel`, `table-wrap`, `eyebrow`, `muted`, `status-pill`) that are defined nowhere in `main.css`/`work.css` — pages render but with zero styling.

**Database / API**
- No `JSONB` anywhere in the schema — every JSON column uses the plain `json` type, giving up GIN indexing and binary storage.
- No partitioning/archival strategy for any of the several unbounded, trigger-protected append-only event tables.
- `accounts` carries both a free-text `custodian` string and a normalized `custodian_id` FK with nothing enforcing agreement; the uniqueness constraint is keyed off the legacy string field.
- `tax_engagement_returns` carries two independent status tracks (`status`, `filing_status`) plus a separate event-history table, with no DB-level mechanism keeping them in sync.
- API response envelopes are unique per router (17+ distinct top-level key conventions found across list endpoints) with zero `response_model` declarations anywhere — no enforced schema for any JSON endpoint.
- Three incompatible page-rendering conventions coexist in `app/templates/` (raw f-string HTML, standalone self-contained templates, proper `{% extends %}` composition) — only the Epic 5 feature area uses shared layout.

---

## Low Priority Cleanup

- Unused imports in `app/security/policy.py`, `app/security/service.py`.
- Misleading "password reset" naming for a flow that issues a token, not a password, in the portal (arguably a positive from a security standpoint, but confusing for future engineers/auditors).
- `assignment_role` (API/service param) vs. `assignment_type` (DB column) naming seam in work management.
- Inconsistent exception types for logically equivalent authorization failures (`ValueError` vs. `PermissionError`) across the workflow and tax-review approval paths.
- `force=True` escape hatch on `transition_return()` is defined but unreachable from any route.
- Duplicate `Transition` Pydantic model name used independently in `workflows.py` and `tax_returns.py`.
- `__import__('datetime')` inline calls in `app/routes/portal.py:117` instead of a top-level import.
- Unused `NotificationCreate` Pydantic model with no corresponding route.
- `app/database/database.py` is an effectively empty file sitting in the same package as `schema.py`.
- Stale docstring/`down_revision` mismatch in `migrations/versions/5bd72a4cc901_add_relationship_intelligence.py`.
- `tax_lots` (portfolio cost-basis) shares the `tax_%` table-name namespace with the entire unrelated tax-return-preparation subsystem, inviting confusion.
- Dense, near-unreadable single-line-per-function code style in `app/services/portfolio.py`, `portfolio_import.py`, and the three tax services — a real (Medium, not Low) maintainability cost given it already correlates with at least two of the correctness bugs found above (H11, H12), but listed here as a cleanup item since fixing style alone doesn't fix behavior.
- ~6 duplicated, near-identical "table is append-only" PL/pgSQL trigger functions that could be one parameterized function.
- No manual mail-sync trigger endpoint (calendar and documents both have one).
- Inconsistent HTML-error-response shape between Microsoft 365 review-queue route files.

---

## Technical Debt Register

| Item | Subsystem | Description | Rough Effort |
|---|---|---|---|
| TD-1 | Auth | Three divergent record-scope implementations (`has_record_scope`, `_scope_filter`, `authorized_assignments`) | M |
| TD-2 | Auth | Dual authorization layers (middleware regex + per-route capability) not co-designed | M |
| TD-3 | M365 | Dead parallel Graph client (`app/connectors/microsoft365/*`, ~600 lines) | S (delete) |
| TD-4 | M365 | No token refresh / plaintext token storage | M |
| TD-5 | M365 | Single-global-account sync model despite multi-account schema support | L |
| TD-6 | Work Mgmt | Automation-action execution engine unwired; escalation feature write-only with no consumption path | L |
| TD-7 | Work Mgmt | Two disconnected task-assignment systems (`tasks.assigned_to` vs `record_assignments`) | M |
| TD-8 | Tax | Filing-provider abstraction orphaned; never called from `record_filing()` | S |
| TD-9 | Tax | Duplicated, drifted context-loader queries between intake and lifecycle services | M |
| TD-10 | Portal | E-signature module (`app/portal/signatures.py`) fully dead code | S (delete or wire up) |
| TD-11 | Portal | No functioning returning-user login or password-reset delivery | L |
| TD-12 | DB Schema | `app/db.py` (reflection) vs `app/database/schema.py` (partial static models) split | L |
| TD-13 | DB Schema | ~48 missing indexes across nearly every table | M (mechanical, but needs careful migration + `CREATE INDEX CONCURRENTLY` planning for prod) |
| TD-14 | DB Schema | ~16 missing constraints (CHECK/UNIQUE/FK, polymorphic entity_type validation) | M |
| TD-15 | DB Schema | No `JSONB`; every JSON column uses `json` | M (schema-wide, mechanical) |
| TD-16 | DB Schema | No partitioning/archival strategy for unbounded append-only event tables | L |
| TD-17 | API | No shared response envelope / `response_model` anywhere; 17+ distinct list-response shapes | L |
| TD-18 | API | No shared "load entity or 404" dependency — copy-pasted ~10+ times | S |
| TD-19 | API | No pagination convention anywhere except one ad hoc `limit` param on `/admin/audit` | M |
| TD-20 | Templates | Three incompatible page-rendering conventions (raw f-string / standalone / `{% extends %}`) | L |
| TD-21 | Dashboards | Dead "Unassigned" and "pending matches" KPIs (H11, H14) | S |
| TD-22 | Perf | N+1 patterns in 5+ dashboards (work items, tax intake staff dashboard, portal dashboard, portfolio search) | L |
| TD-23 | Code style | Dense, uncommented single-line-per-function style in portfolio and tax services correlates with undetected bugs | M (style pass + targeted tests) |

*(S = days, M = 1-2 weeks, L = 2-4+ weeks, each assuming one engineer; see "Estimated Effort" below for roll-up.)*

---

## Recommended Refactors Before Release 1.0

1. **Unify record-level scoping into one shared service** (resolves TD-1). Replace `has_record_scope`, `_scope_filter`, and `authorized_assignments` with a single `authorize_record(principal, entity_type, entity_id, mode="read"|"write") -> bool` used everywhere, including the tax and work-management routers. This single change also closes H3, H5, H6, and H8's root cause.
2. **Reconcile the middleware/route authorization layers** (resolves TD-2, H4). Either (a) make `require_capability(...)` on the route the sole source of truth and reduce middleware to authentication-only, or (b) generate the middleware `RULES` table from route-declared capabilities so they cannot drift. Add a test that fails CI if a new route's declared capability doesn't match its middleware-inferred capability.
3. **Add an ownership/ceiling check to role composition and self-assignment endpoints** (resolves H1, H2, H8): a principal may only grant a capability/assignment they themselves hold, and self-assignment of `user_id` on `/api/v1/work/assignments` should require the dedicated `assignment.manage` capability, matching the existing `/admin/assignments` gate.
4. **Consolidate the two tax context-loaders** (resolves H12, TD-9) into one shared `tax_return_context(connection, return_id)` used by both `tax_intake.py` and `tax_return_lifecycle.py`, with one correct household-resolution rule.
5. **Batch-migrate missing indexes and constraints** (resolves H20, H21, TD-13, TD-14) as one dedicated migration sprint — group by table, use `CREATE INDEX CONCURRENTLY` in production, and add CHECK constraints/lookup tables for the highest-traffic status columns first (`tasks.status`, `documents.review_status`, `workflow_instances.status`).
6. **Adopt a single response-envelope and pagination convention** (resolves TD-17, TD-19) — e.g. `{"data": [...], "meta": {"total": N, "limit": L, "offset": O}}` — and retrofit it to at least the Epic 5 and portal routers before exposing any external/frontend client against this API.
7. **Delete or finish the three orphaned modules** (resolves TD-3, TD-8, TD-10): remove the dead Microsoft connector client, either wire `tax_filing_providers.py` into `record_filing()` or delete it, and either route `portal/signatures.py` into the e-file authorization flow or remove it and update the docs that claim e-signature is supported.
8. **Fix the two silently-dead dashboard KPIs** (resolves H11, H14, TD-21) and add regression tests asserting on the actual metric values, not just happy-path flow, to prevent recurrence.
9. **Resolve the `app/db.py` / `app/database/schema.py` split** (resolves TD-12, H22) — pick one pattern (recommend: static `Table` models as the source of truth, migrations generated/verified against them) and backfill Python models for the ~50% of tables that currently exist only as raw DDL.
10. **Build a real Microsoft 365 token-refresh path and encrypt tokens at rest** (resolves H10, TD-4) before any further reliance on the integration — today it is architected to silently stop working roughly every hour.

---

## Estimated Effort

Rough sizing assuming a small team (2-3 engineers) working sequentially through priority order, not all in parallel:

| Workstream | Items covered | Estimate |
|---|---|---|
| Security hardening sprint (H1-H10) | Privilege escalation fixes, IDOR fixes, portal permission enforcement, token encryption | **1.5–2 weeks** |
| Correctness fixes (H11-H14) | Dead metrics, household-context drift, document-matching guard | **3–5 days** |
| Scalability pass (H15-H19) | N+1 fixes, dashboard query batching, pagination on the worst offenders | **1.5–2 weeks** |
| Database consistency migration (H20-H22, TD-13/14/15) | Missing indexes, missing constraints, JSONB migration | **2–3 weeks** (includes careful production rollout planning) |
| Authorization architecture consolidation (TD-1, TD-2, refactor #1-#3) | Unified scope service, middleware/route reconciliation | **2–3 weeks** |
| API consistency pass (TD-17-TD-19, refactor #6) | Shared envelope, shared 404 dependency, pagination convention | **1.5–2 weeks** (larger if a frontend contract must be preserved/versioned) |
| Dead-code resolution (TD-3, TD-8, TD-10) | Delete or finish 3 orphaned modules | **3–5 days** |
| M365 hardening (H10, TD-4, TD-5) | Token refresh, encryption, multi-account model | **2–3 weeks** |
| Portal completion (portal login/reset, document download, TD-11) | Functional gaps in the client-facing surface | **1.5–2 weeks** |
| Schema documentation consolidation (TD-12) | Resolve `db.py`/`schema.py` split, backfill models | **2–3 weeks** |
| **Total (sequential, one team)** | | **~16–22 weeks** |
| **Total (parallelized across 2-3 workstreams at once)** | | **~8–12 weeks** |

The security hardening sprint (H1-H10) should not be deferred or parallelized away — it is the one workstream with active exploitability today and should gate any further external (client-facing portal) rollout.

---

## Risk Assessment

| Risk | Likelihood | Impact | Notes |
|---|---|---|---|
| Privilege escalation exploited internally (H1, H2) | Medium | High | Requires only capabilities already broadly granted to `advisor`/`operations`/any `role.manage` holder; no external attacker needed — an internal actor or a compromised staff account is sufficient |
| Cross-client data exposure via IDOR (H5, H6, H3) | Medium | High | Directly affects client confidentiality in a wealth/tax practice context — the exact domain where this is most consequential |
| Portal permission bypass exposed to actual clients (H7) | Low today (portal not yet in wide external use per H11/portal gaps below) | High if portal usage scales | Should be fixed before any broader client-facing rollout — compounds with the fact that login/reset don't fully work yet (TD-11), which is incidentally limiting current exposure |
| Cross-client document misassignment (H13) | Low-Medium | High | Wealth/tax documents auto-matched by substring containment; a real business risk, not just a data-quality issue |
| Microsoft 365 integration silently stops working (H10 refresh gap) | High (near-certain within hours of any connection) | Medium | Already happening in practice; degrades data freshness (timeline/calendar/mail sync) rather than causing an outage, but silently |
| Performance degradation as data grows (H15-H20) | High, but gradual | Medium | Not urgent today at current data volumes per repo evidence (dozens-to-hundreds of rows in review/test databases), but will become user-visible well before the firm reaches meaningful scale if untreated |
| Dead-KPI-driven bad business decisions (H11, H14) | Already occurring | Low-Medium | Two homepage metrics have been silently wrong since they shipped; low technical risk, real business-trust risk once discovered |
| Schema/API consolidation debt compounding (TD-12, TD-17) | Certain if deferred | Medium, compounding | Every new sprint that doesn't use the unified patterns makes the eventual consolidation more expensive — this is the "if not now, worse later" category |

**Overall risk posture:** the application is safe to continue developing against internally, but the confirmed privilege-escalation and IDOR findings (H1, H2, H3, H5, H6, H8) should be treated as release blockers for any expanded internal rollout, and the portal permission gap (H7) plus the still-incomplete portal login/reset flow should gate any external client-facing launch beyond the current limited/invitation-only usage.

---

## Top 25 Improvements — Ranked by Impact vs. Effort

Ranked with "quick, high-impact wins" first, descending toward larger structural investments. Impact and Effort are both rated High/Medium/Low.

| Rank | Improvement | Impact | Effort | Resolves |
|---|---|---|---|---|
| 1 | Add ownership/scope check to `/api/v1/work/assignments` write endpoints; require `assignment.manage` for arbitrary `user_id`/`entity_id` grants | High | Low | H1, H8 |
| 2 | Add `_authorized()`/record-scope check to the two unprotected tax-return endpoints (`api_review_decision`, `api_resolve`) | High | Low | H3 |
| 3 | Fix `end_relationship` to verify the relationship actually belongs to the authorized `person_id` | High | Low | H5 |
| 4 | Add `require_scope(..., permission="messages")` to portal `send_message`/`list_messages`/`mark_read` | High | Low | H7 |
| 5 | Scope the two "available people" picker queries to the caller's authorized record set | High | Low | H6 |
| 6 | Fix `i970d9f7b8c9` to `ALTER COLUMN status SET DEFAULT 'received'` and add a regression test asserting the "Unassigned" metric | Medium | Low | H11 |
| 7 | Fix the "pending matches" dashboard metric to count actual unreviewed groups instead of a never-persisted decision value | Medium | Low | H14 |
| 8 | Add a subset/ceiling check to `compose_role`/`assign_role` so a principal can't grant capabilities they don't hold | High | Medium | H2 |
| 9 | Add office/assignment scope filtering to `process_reminders()` | High | Low-Medium | H9 |
| 10 | Consolidate `tax_intake._return_context` and `tax_return_lifecycle._context` into one correctly-coalescing shared loader | Medium-High | Medium | H12, TD-9 |
| 11 | Reconcile middleware `RULES` capability inference with route-declared capabilities (start with `tax.review`/`tax.write` and `work.approve`/`work.write`) | High | Medium | H4 |
| 12 | Encrypt Microsoft OAuth tokens at rest (application-level, e.g. Fernet with a KMS-backed key) | High | Medium | H10 |
| 13 | Implement Microsoft Graph token refresh (`acquire_token_by_refresh_token`) | High | Medium | H10, TD-4 |
| 14 | Add the missing CSS classes referenced by the tax dashboards, or restyle them onto existing `work.css` classes | Medium | Low | (tax templates render unstyled) |
| 15 | Batch-add missing indexes on the highest-traffic hot-path columns (`people.household_id`, `tasks.person_id`, `activities.person_id`, `documents.person_id`, `timeline_events.person_id`/`household_id`, `portal_notifications.portal_account_id`, `portal_threads.household_id`/`person_id`) | High | Medium | H20 (partial) |
| 16 | Rewrite `work_items()` to filter by SQL predicate (caller's scope) before loading rows, not after | High | Medium | H15 |
| 17 | Fix `staff_dashboard()`/`api_detail()` N+1 in tax intake using the same bulk-query pattern already used by `production_dashboard()` | High | Medium | H16 |
| 18 | Cache/thread a single `portal_scope()` computation through one portal `dashboard()` render instead of recomputing it 4 times | Medium-High | Medium | H17 (partial) |
| 19 | Add pagination (`limit`/`offset`) to the firm-wide unbounded dashboards (`activity_dashboard`, `task_dashboard`, relationship/portfolio search) | High | Medium | H18 |
| 20 | Unify record-scope logic into one shared `authorize_record()` service replacing `has_record_scope`/`_scope_filter`/`authorized_assignments` | High | Medium-Large | TD-1, root cause of H3/H5/H6/H8 |
| 21 | Add CHECK constraints / lookup-table enforcement to the highest-traffic free-text status columns (`tasks.status`, `documents.review_status`, `workflow_instances.status`) | Medium | Medium | H21 (partial) |
| 22 | Delete the dead Microsoft connector client and either wire up or delete `tax_filing_providers.py` and `portal/signatures.py` | Medium | Low-Medium | TD-3, TD-8, TD-10 |
| 23 | Build a functioning portal returning-user login and complete the password-reset delivery path | High | Medium-Large | TD-11 |
| 24 | Adopt one shared API response envelope and pagination convention, retrofit to Epic 5 + portal routers first | Medium-High | Large | TD-17, TD-19 |
| 25 | Resolve the `app/db.py`/`app/database/schema.py` split; backfill Python `Table` models for the ~50% of tables that only exist as migration DDL | Medium | Large | TD-12, H22 |

---

*Review conducted as RC8, prior to Sprint 5.4. No application code was modified as part of this review. This document should be treated as a living backlog — re-run the relevant subsystem passes after each hardening sprint to confirm closure.*
