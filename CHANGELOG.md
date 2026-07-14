# Changelog

All notable Client360 releases are documented here.

## [Unreleased]

No unreleased application changes.

## [0.9.9] — 2026-07-14

Platform Consolidation — a security, performance, and production-readiness
release with no new end-user features. See
[Release 0.9.9 Notes](docs/RELEASE_0.9.9.md) and
[RC12 Validation](docs/RC12_VALIDATION.md).

### Security

- Microsoft 365 OAuth tokens encrypted at rest (Fernet-encrypted MSAL cache keyed
  by `MICROSOFT_TOKEN_KEY`) with a durable `acquire_token_silent` refresh
  lifecycle; crypto fails closed when the key is absent; no plaintext token is
  written to the database or logs.
- Delegated Graph scopes reduced to least-privilege read-only (no `Mail.Send`, no
  `*.ReadWrite`).
- CSRF defense-in-depth: `Referer` fallback added to the `Origin` check.
- Config hardening: production boot fails without `SESSION_SECRET`; startup warns
  on a development fallback or a missing `MICROSOFT_TOKEN_KEY`.

### Performance

- 24 hot-path foreign-key indexes (built `CONCURRENTLY`, reversible) making the
  client/household/portal/workflow read paths index-bound.
- Eliminated four verified N+1 / full-scan hot paths (intake dashboard 28→7,
  concentration filter 28→2, portal `/notifications` 21→1, `work_items()`
  authorization pushed into SQL → O(caller's book)), preserving output and
  authorization semantics.

### Changed

- Consolidated the Microsoft Graph connector onto a single delegated path and the
  portal provider registries onto one canonical `ProviderRegistry`.
- Per-account Microsoft sync-health surfaced on `/microsoft365/status` and the new
  `/readiness` endpoint.

### Added

- `GET /readiness` (DB, Alembic head drift, scheduler, sync-health; 200/503);
  `GET /health` remains DB-independent liveness.
- Backup/restore runbook and rehearsal script.

### Removed

- `POST /timeline/test` debug endpoint, the unused app-only Graph connector
  modules, and verified-unused imports across 18 files.

### Migrations

- `m3d14a2f1e0c` (token security columns), `n4e25b3c2f1d` + `o5f36c4d3e2a`
  (hot-path indexes). Additive and reversible; single head `o5f36c4d3e2a`.

## [0.9.8] — 2026-07-14

Sprint 5.4 — Tax Document Intelligence & Missing Information. See
[Release 0.9.8 Notes](docs/RELEASE_0.9.8.md) and
[Tax Document Intelligence](docs/SPRINT_5_4_TAX_DOCUMENT_INTELLIGENCE.md).

### Added

- Deterministic tax document matching engine (exact identifiers, confidence
  scoring, ambiguity floor) with mandatory human review for anything not
  deterministically resolved. Replaces the substring-based Microsoft document
  matching (RC8 H13).
- Authorization-aware ownership validation and record-scope-checked reviewer
  actions (accept/reject/reassign/classify/duplicate/revert) with immutable,
  append-only review and evidence ledgers.
- Missing-information engine that recomputes from accepted document links and
  drives the existing checklist / portal-request / workflow-gating mechanisms.
- Staff document-review workspace and `/api/v1/tax/documents` + checklist/missing
  APIs; new `tax.document.review` capability and four document review queues.
- AI classifier port (interface only; inert — no vendor, no external call).
- Shared tax dashboard stylesheet (`tax.css`), closing an RC8 unstyled-class gap.

- RC11 remediation: wired ingestion end-to-end — portal uploads and Microsoft
  documents now flow through the engine (dual-source links reference either a
  canonical or a Microsoft document, no binary duplicated); made ingestion
  idempotent; added review-state guards (HTTP 409 on stale actions); re-validate
  document owner vs return client on accept/reassign (HTTP 403 + denied audit);
  and persist unmatched documents reviewably without fabricating ownership.

### Database

- Added `tax_document_links`, `tax_document_classifications`,
  `tax_document_match_evidence`, `tax_document_review_events` (append-only), the
  `tax.document.review` capability, four review queues, and the
  `tax_missing_items` FK index (RC9 H20); legacy free-text Microsoft matching
  rules deactivated. RC11 remediation adds a dual-source link model (nullable
  `document_id` + `microsoft_document_id` with an exactly-one-source CHECK) and a
  nullable return for unmatched links. Parent `j0a81f9c8d7e`; new head
  `l2c03f1e0d9b`.

### Security

- Eliminated all substring/containment ownership matching for tax documents
  (H13). Auto-assignment requires a single exact-identifier candidate above the
  auto-match threshold with no competing candidate above the ambiguity floor.

### Validation

- 136 automated tests passed; independent RC11 adversarial validation and retest
  (43/43 checks) confirmed H13 cannot be recreated across nine datasets and that
  the RC11 remediation introduced no new gap (SAFE TO MERGE). Clean installation,
  v0.9.7 upgrade/downgrade/re-upgrade, and sentinel preservation validated. See
  [RC11 Validation](docs/RC11_VALIDATION.md) and [RC11 Retest](docs/RC11_RETEST.md).

## [0.9.7] — 2026-07-14

Security hardening release. Fixes the confirmed, RC9-verified authorization,
record-scope, and workflow-permission defects before Sprint 5.4. No new feature
work; least privilege, immutable audit, and record-level authorization
preserved. See [Security Hardening 0.9.7](docs/SECURITY_HARDENING_0.9.7.md).

### Security

- Fixed work-assignment privilege escalation: assigning a client record now
  requires `assignment.manage` plus record scope, separated from ordinary
  `work.write` mutation (H1); reassign/remove now enforce assignment ownership
  (H8).
- Fixed role-composition privilege escalation: `role.manage` can only grant
  capabilities it holds and cannot assign a more-powerful role or recompose the
  protected administrator role (H2).
- Enforced record-scope authorization consistently on tax return review and
  correction endpoints (H3).
- Corrected the middleware/route capability mismatch that locked the compliance
  role out of workflow approvals (H4).
- Required authorization over a relationship's owning record before
  deactivation (H5).
- Scoped client-profile pickers to prevent firm-wide name/email enumeration
  (H6).
- Enforced the portal `messages` grant on secure-message read/send/mark-read
  with default-deny (H7).
- Restricted the firm-wide reminder trigger to firm-wide record authority (H9).

### Fixed

- Rewrote the always-zero "Unassigned" tax dashboard metric (H11) and the
  always-zero "pending matches" dashboard metric (H14).
- Eliminated a duplicate database connection pool created at startup via the
  `person_merge` import chain (H22, narrow fix).

### Added

- Canonical record-scope authorization service (`app/security/authorization.py`)
  and 20 authorization regression tests.
- Immutable `outcome="denied"` audit events for denied high-risk mutations.

### Database

- Migration `j0a81f9c8d7e` aligns `tax_engagement_returns.status` server default
  to `received` (parent `i970d9f7b8c9`; new head `j0a81f9c8d7e`).

### Validation

- 94 automated tests passed (74 existing + 20 new), clean installation, v0.9.6
  upgrade/downgrade/re-upgrade, sentinel preservation, startup, route, OpenAPI,
  template, authorization-matrix, and immutable-audit validation.
- Independent RC10 adversarial validation passed (52/52 attack cases blocked;
  no unintended regressions; SAFE TO MERGE). See
  [RC10 Validation](docs/RC10_VALIDATION.md).

## [0.9.6] — 2026-07-14

### Added

- Canonical 15-state tax return lifecycle with immutable transition history.
- Preparer, manager, and partner reviews linked to the existing independent
  approval engine, including corrections and return-to-preparer behavior.
- Portal return approval, e-file authorization, delivery acknowledgement,
  provider-neutral filing events, nine production queues, four dashboards, and
  versioned staff/portal APIs.

### Database

- Added five production tables and ten return lifecycle/filing columns with
  parent `h860c8e6a7b8`; new head `i970d9f7b8c9`.

### Validation

- Added the Tax Return Lifecycle architecture and PR #16 RC7 validation
  record.
- Passed 74 automated tests, clean installation, v0.9.5 upgrade/downgrade/
  re-upgrade, sentinel preservation, startup, route, OpenAPI, and template
  validation.
- Found and fixed two template defects during release-candidate validation:
  a missing shared staff base template and a Jinja/dict-key collision on the
  production dashboard.

## [0.9.5] — 2026-07-14

### Added

- Versioned engagement-letter, organizer, questionnaire, and document-checklist
  templates with immutable published definitions and launch-time snapshots.
- Tax intake orchestration, saved progress, conditional/required questions,
  missing-information tracking, portal completion, daily reminders, readiness
  dashboards, and automatic workflow advancement.
- Versioned staff and portal APIs for tax intake, backed by existing document,
  notification, assignment, queue, timeline, audit, and authorization services.

### Database

- Added 12 intake tables with parent revision `g750b7d5f6a7`; new head
  `h860c8e6a7b8`.

### Validation

- Added the Tax Engagement Intake architecture and RC6 validation report.
- Passed 69 automated tests, clean installation, v0.9.4 rollback/re-upgrade,
  sentinel preservation, startup, route, OpenAPI, and template validation.

## [0.9.4] — 2026-07-14

### Added

- Provider-neutral tax firms, offices, staff office roles, tax years, seasons,
  filing jurisdictions, return types, filing statuses, engagements, returns,
  calendars, versioned deadline rules, and workflow links.
- Authorized tax production dashboard and versioned `/api/v1/tax` reference,
  dashboard, engagement, and deadline operations.
- Five reusable tax work queues, four tax capabilities, eight baseline return
  types, six filing statuses, and a versioned Tax Engagement Foundation workflow.
- Automatic engagement workflow generation with existing assignment, queue,
  timeline, immutable audit, and record-level authorization integration.

### Documentation

- Added the nine-sprint Epic 5 Tax Practice Platform technical design.
- Defined normalized tax, workflow, portal, document, provider, security,
  reporting, migration, testing, and Release 1.0 readiness architecture.
- Added Tax Domain Foundation operating documentation and the RC5 release
  validation report.

### Database

- Alembic head: `g750b7d5f6a7`.
- Added 14 normalized tax-domain tables while preserving Release v0.9.3 data.

## [0.9.3] — 2026-07-14

### Added

- Separate portal identities, household/delegated grants, invitations,
  MFA-ready sessions, password-reset handoff, and device tracking.
- Secure client messaging, internal-note isolation, attachments, and receipts.
- Document requests, upload versions, approvals, client workflow tasks,
  notifications, and provider-neutral e-signature abstractions.
- Versioned portal APIs and eight portal pages.

### Security

- Portal accounts and sessions are isolated from staff identities.
- Self-only, joint, trusted-contact, and delegated household grants are
  explicitly scoped and time bounded.
- Messages, read receipts, route mutations, and security events are audited;
  client-visible queries exclude internal staff notes.

### Database

- Alembic head: `f640a6c4e5f6`.
- Added 15 portal identity, access, session, collaboration, notification, and
  signature-request tables without changing Release 0.9.2 data.

## [0.9.2] — 2026-07-14

### Added

- Immutable, versioned workflow templates with complete launch-time snapshots.
- Dependency-aware sequential, parallel, and conditional workflow execution.
- Pause, resume, cancel, complete, and reopen controls.
- Independent approval routing with segregation-of-duties enforcement.
- SLA escalation processing and five-minute scheduler automation.
- Event-driven triggers and an idempotent automation action ledger.
- Workflow UI, metrics, reporting data, and `/api/v1/workflows` APIs.
- Twelve published templates for prospecting, onboarding, Schwab operations,
  transfers, reviews, tax, estate, insurance, termination, and compliance.

### Changed

- Workflow-instance assignments now authorize and expose child workflow steps
  in My Work.
- Published template definitions and workflow/audit event ledgers are protected
  by database triggers.

### Database

- Alembic head: `e530f5b3d4e5`.
- Added seven tables for templates, dependencies, events, triggers, actions, and
  escalations.
- Added execution snapshots and lifecycle metadata to Release 0.9.1 workflow
  records without replacing existing data.

## [0.9.1] — 2026-07-14

- Added Operational Work Management, assignments, reusable queues, My Work,
  Team Work, capacity, SLA risk, and versioned work APIs.
- Alembic head: `d420f4a2c3d4`.

## [0.9.0] — 2026-07-14

- Integrated Microsoft 365, Relationship Intelligence, Schwab Portfolio
  Intelligence, firm identity, capability authorization, and immutable audit.
- Alembic head: `c410f4a1b2c3`.
# Unreleased

### Added

- Sprint 5.1 Tax Domain Foundation: tax firms and offices, staff office roles,
  tax years and seasons, jurisdictions, return types and filing statuses,
  engagements and returns, versioned deadlines, tax queues, a production
  dashboard, versioned APIs, and automatic workflow generation.
- Tax-domain capability and record-level authorization, timeline publication,
  and immutable audit integration using the existing Client360 platforms.
