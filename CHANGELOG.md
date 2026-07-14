# Changelog

All notable Client360 releases are documented here.

## [Unreleased]

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
