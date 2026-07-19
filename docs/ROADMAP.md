# Client360 Roadmap

> **HISTORICAL — Legacy product roadmap (v0.9.x–1.0).** This is the legacy application's
> product roadmap; its epic numbering (e.g. *Legacy Epic 4 — Practice Management*, *Legacy
> Epic 5 — Tax Practice Platform*) is **frozen and distinct** from the active ADR-driven
> re-implementation track. Legacy "Epic N" ≠ current-track "Epic N". For the active track
> and its epic sequence, see [`architecture/REIMPLEMENTATION_ROADMAP.md`](architecture/REIMPLEMENTATION_ROADMAP.md)
> (governed by [ADR-014](architecture/adr/ADR-014-engineering-backlog-and-roadmap-governance.md)).
> Retained unchanged for historical record.

## Release 0.9 — Epic 4 Foundation ✅

- PostgreSQL and a single linear Alembic migration history
- Canonical people, households, source matching, documents, tasks, and activities
- Client timeline, search, dashboard, and unified Client Workspace
- Microsoft Outlook mail and calendar synchronization
- SharePoint and OneDrive document metadata synchronization
- Unmatched Microsoft mail, attendee, and document review workflows
- Relationship Intelligence graph for family, professional, business, trust,
  estate, beneficiary, and household relationships
- Schwab Portfolio Intelligence for accounts, holdings, cash, lots,
  beneficiaries, performance, billing, and household rollups
- Capability-based roles, teams, assignments, record-level authorization,
  managed-identity adapters, sessions, and immutable audit logging
- Clean-database installation and upgrade compatibility from the prior `main`
  migration head

## Release 0.9.1 — Operational Work Management ✅

- Generic user and team assignments across people, households, tasks,
  documents, workflow instances, and workflow steps
- Primary, secondary, supervisor, and team ownership with immutable history
- Deterministic automatic assignment rules
- Reusable secure operational queues
- My Work, Team Work, queue, workload, capacity, SLA, and bottleneck views
- Versioned work-management APIs for current Jinja and future React/mobile clients
- Assignment timeline events, immutable audit records, and least-privilege filtering
- Clean installation and upgrade compatibility from Release 0.9

## Release 0.9.2 — Workflow and Process Automation ✅

- Immutable versioned templates and execution snapshots
- Sequential, parallel, conditional, and dependency-aware execution
- Pause, resume, cancel, complete, and reopen lifecycle controls
- Independent approvals and segregation-of-duties enforcement
- SLA evaluation, idempotent escalations, and five-minute scheduler processing
- Event-driven workflow triggers and idempotent automation actions
- Workflow assignment, queue, timeline, audit, authorization, and record-scope integration
- Workflow UI, reporting metrics, and versioned APIs
- Twelve published firm workflow templates
- Clean installation and upgrade compatibility from Release 0.9.1

## Release 0.9.3 — Client Portal and Secure Collaboration ✅

- Separate portal identities, invitations, MFA-ready sessions, devices, and resets
- Self, joint, trusted-contact, and delegated household authorization
- Secure messages, attachments, immutable receipts, and internal-note isolation
- Document requests, versioned uploads, approvals, and client workflow tasks
- In-app notifications and disabled provider hooks for email, SMS, and push
- Provider-neutral e-signature request and event architecture
- Versioned portal APIs and eight portal pages
- Clean installation and upgrade compatibility from Release 0.9.2

## Release 0.9.4 — Tax Domain Foundation ✅

- Canonical tax firms, offices, staff tax roles, years, and seasons
- Filing jurisdictions, return types, filing statuses, engagements, and returns
- Versioned calendars and configurable deadline rules
- Tax work queues, assignments, dashboard metrics, and versioned APIs
- Automatic versioned workflow generation for new tax engagements
- Existing timeline, immutable audit, capability, and record-scope integration
- Clean installation and upgrade/downgrade compatibility from Release 0.9.3

## Release 0.9.5 — Tax Engagement Intake & Client Collaboration ✅

- Immutable versioned engagement letters and portal acceptance evidence
- Individual/business organizers with tax-year snapshots and saved progress
- Required and conditional questionnaires with resumable answers
- Required/optional document checklists linked to portal uploads and versions
- Missing-information tracking, reminders, and readiness dashboards
- Automatic advancement of existing tax intake and document workflow steps
- Provider-neutral notifications, timeline, audit, and record authorization
- Clean installation and upgrade/downgrade compatibility from Release 0.9.4

## Release 0.9.6 — Tax Return Lifecycle & Production Automation ✅

- Canonical 15-state return lifecycle with immutable transition history
- Preparer, manager, and partner review routing on the existing independent
  approval engine, with corrections and return-to-preparer behavior
- Portal return approval, e-file authorization, and delivery acknowledgement
- Provider-neutral filing status machine and filing event ledger
- Nine production work queues and four staff dashboards
- Versioned staff/portal APIs and automatic workflow-milestone advancement
- Clean installation and upgrade/downgrade compatibility from Release 0.9.5

## Release 0.9.7 — Security Hardening ✅

- Fixed work-assignment privilege escalation and assignment IDOR (H1, H8)
- Fixed role-composition privilege escalation and protected the administrator
  role (H2)
- Enforced record-scope authorization consistently across tax return review,
  correction, and lifecycle endpoints (H3)
- Corrected the middleware/route capability mismatch locking out compliance
  approvals (H4)
- Closed the relationship-deactivation IDOR (H5)
- Scoped client-profile pickers against firm-wide enumeration (H6)
- Enforced portal secure-messaging permission grants with default-deny (H7)
- Restricted the firm-wide reminder trigger to firm-wide authority (H9)
- Repaired two always-zero dashboard metrics (H11, H14) and removed a duplicate
  connection pool (H22, narrow)
- Added a canonical record-scope authorization service, denial audit events,
  and 20 authorization regression tests
- Clean installation and upgrade/downgrade compatibility from Release 0.9.6

See [Security Hardening 0.9.7](SECURITY_HARDENING_0.9.7.md) and
[RC9 Architecture Verification](RC9_ARCHITECTURE_VERIFICATION.md). Deferred
follow-ups (Microsoft 365 token encryption, performance/index work, DB
constraints, and full authorization consolidation) remain scheduled for
Releases 0.9.8 and 1.0 per RC9.

## Release 0.9.9 — Platform Consolidation ✅

Security, performance, and production-readiness consolidation (no new features),
delivered as eight independently reviewed phases and RC12-validated (0 defects).

- Microsoft 365 OAuth tokens encrypted at rest (Fernet MSAL cache) with a durable
  silent-refresh lifecycle and least-privilege read-only scopes; per-account
  sync-health surfaced (addresses the deferred token-encryption follow-up).
- 24 hot-path foreign-key indexes (`CONCURRENTLY`, reversible) and elimination of
  four verified N+1 / full-scan hot paths (addresses the deferred performance/index
  work).
- Consolidated the Graph connector and portal provider registries; removed a debug
  endpoint and unused imports.
- Production readiness: `/readiness` probe, startup config validation, CSRF
  defense-in-depth, and a rehearsed backup/restore runbook (advances the 1.0
  backup/restore and observability items).
- Additive, reversible migrations; single Alembic head `o5f36c4d3e2a`. Clean
  upgrade/downgrade compatibility from Release 0.9.8.

See [Release 0.9.9 Notes](RELEASE_0.9.9.md), [Production Architecture](PRODUCTION_ARCHITECTURE.md),
[Deployment Runbook](RELEASE_0.9.9_DEPLOYMENT_RUNBOOK.md), and
[RC12 Validation](RC12_VALIDATION.md). Deferred: advisor-notes-to-DB migration,
`MICROSOFT_TOKEN_KEY` rotation, legacy plaintext-column removal, and the orphaned
`app/models/` scaffold.

## Release 0.9.11 — Employer Operations & Employee Benefits 🔶 (release candidate; PR #22 draft, not tagged)

Usable **Employer Operations** product on shared Client360 concepts (ADR-18) with **Employee
Benefits + Retirement** first-class, delivered as eight independently reviewed phases and
[RC14](RC14_VALIDATION.md)-validated (**SAFE TO MERGE**, 0 defects).

- **Organizations** = existing `relationship_entities` (+`organization_profiles`, EIN
  encrypted); permanent relationship roles; typed ownership on the existing `relationships`
  edge; service lines; universal `engagements` model (tax converges later, documented).
- **Benefits + retirement** first-class (17 plan types); plans/plan-years/employments/
  enrollments/deferral elections; provider-neutral ports (Betterment seeded; **integrations
  disabled**).
- 18 detectors + a date-driven obligation model (**verified dates only, nothing inferred**);
  benefits reuses the platform Exception Engine (`domain='benefits'`), the shared SLA sweep,
  Work Management + seven queues, and the scheduler (overlap-prevented scan).
- Staff API + `/organizations`/`/benefits`/`/benefits/reporting` consoles (names not IDs;
  EIN gated); org-scoped **employer portal** (PII-free allowlist, census upload, messages,
  auditable notifications) reusing the existing portal stack; proportional benefits dashboard
  reusing `exception_reporting`.
- New `organization.*` / `benefits.*` capabilities + `benefits_*` roles; no role widened; no
  new `record.read_all`. Additive/reversible migrations; single head `u1f9c0i9h8g7`;
  sentinel-preserving up/down cycle from v0.9.10. **Tax untouched.**

See [Release 0.9.11 Notes](RELEASE_0.9.11.md),
[Architecture (ADR-18)](RELEASE_0.9.11_BENEFITS_ARCHITECTURE.md), and
[RC14 Validation](RC14_VALIDATION.md).

## Release 0.9.10 — Exception Engine ✅ (released; tag `v0.9.10`)

Platform-wide **Exception Engine** (ADR-17), implemented **tax domain only**, delivered as
eight independently reviewed phases and [RC13](RC13_VALIDATION.md)-validated (**SAFE TO
MERGE**, 0 defects).

- Canonical engine (`exceptions`/`exception_events`/`exception_types` with a required
  CHECK-constrained `domain`): one state machine, idempotent dedupe, stale-action
  rejection, immutable append-only event ledger, record-scope authorization, audit +
  timeline on every mutation.
- 15 tax detectors (source-of-truth → exceptions; auto-resolve/reopen); deterministic
  replay-safe SLA sweep with honest notification outcomes (email/SMS stubbed → `disabled`).
- Work Management integration + queues (`tax_exceptions`, `tax_exceptions_critical`,
  `compliance_exceptions`); versioned API + staff console; read-only client portal
  "Action Needed" (strict client-visible allowlist, no internal leakage); authorization-
  filtered exception dashboards & reporting (MTTA/MTTR/reopen/SLA/trend — real data only).
- New least-privilege `exception.*` capabilities; no role widened; no new `record.read_all`.
- Additive/reversible migrations; single Alembic head `q7b58f6c5d4e`; sentinel-preserving
  clean upgrade/downgrade compatibility from Release 0.9.9.

See [Release 0.9.10 Notes](RELEASE_0.9.10.md), [ADR-17](ADR_EXCEPTION_ENGINE_SCOPE.md),
[Sprint 5.5 design](SPRINT_5_5_EXCEPTION_DESIGN.md), and [RC13 Validation](RC13_VALIDATION.md).

## Developer Tooling — Developer Demo Mode ✅

Implemented and available for local evaluation (developer tooling; not a numbered
product release). A repeatable, safety-guarded demo with fictional data, isolated to
a `client360_demo` database, reusing the real auth/authorization. Role-aware landing
for all six personas; `scripts/demo.sh` lifecycle commands. See
[Developer Demo Mode](DEVELOPER_DEMO_MODE.md) and
[release notes](DEVELOPER_DEMO_MODE_RELEASE.md). Known non-blocking UX findings tracked
in [Demo UX Review](DEMO_UX_REVIEW.md).

## Release 1.0 readiness

- Production-equivalent managed OIDC and MFA validation
- Controlled Microsoft test-tenant validation, including throttling and token renewal
- Representative sanitized Schwab import validation
- Production-sized migration timing and lock analysis
- Backup and restore rehearsal
- Combined visual, accessibility, and mobile-width review
- Production observability, scheduler alerts, retention, and operational runbooks

## Epic 4 — Practice Management Platform

### Sprint 4.2 — Operational Work Management ✅

- Assignment engine and assignment history
- Reusable secure queues
- My Work and team dashboards
- Capacity, priority, SLA, and daily-agenda services
- Advisor, Operations, Tax, and Management operating views

### Sprint 4.3 — Workflow and Process Automation ✅

- Reusable workflow templates and instances
- Step dependencies, approvals, escalations, and automation triggers
- Client, tax, investment, and document workflow integration

### Sprint 4.4 — Client Portal and Secure Collaboration ✅

- Portal identities, invitations, MFA, and delegated household access
- Secure messaging and document requests
- Client-facing workflow tasks, approvals, and status visibility
- E-signature and notification provider adapters
- Consent, communication preferences, retention, and portal audit controls

Tax-practice work previously proposed as Sprint 4.5 now belongs to Epic 5 so it
can build on the completed practice-management platform without duplicating it.

## Future intelligence and integrations

- Tax Intelligence: Drake, TaxDome migration, IRS notices, and planning data
- Additional custodians and live Schwab acquisition adapter
- QuickBooks and revenue intelligence
- AssetMark and remaining historical import sources
- AI meeting preparation, client briefs, and relationship-aware recommendations
- Client portal and secure document exchange

## Epic 5 — Tax Practice Platform 🚧

1. Tax domain, offices, jurisdictions, deadlines, and workflow launch ✅
2. Engagement intake, organizers, questionnaires, and engagement letters ✅
3. Return lifecycle and production-stage automation ✅
4. Tax document intelligence and missing information ✅
5. Extensions, estimates, notices, and amendments
6. Review, approval, e-file, delivery, and compliance
7. Secure tax portal and client collaboration
8. Drake/provider and IRS transcript integration
9. Production reporting, capacity, AI extensions, and release readiness

Sprint 5.1 shipped in Release v0.9.4, Sprint 5.2 in Release v0.9.5,
Sprint 5.3 in Release v0.9.6, Sprint 5.4 (Tax Document Intelligence) in Release v0.9.8,
and **Sprint 5.5 (Exception Engine, ADR-17 — tax domain first)** in Release v0.9.10
([RC13](RC13_VALIDATION.md)-validated). See
[Epic 5 Technical Design](EPIC_5_TAX_PRACTICE_PLATFORM.md) and
[Tax Domain Foundation](TAX_DOMAIN_FOUNDATION.md),
[Tax Engagement Intake](TAX_ENGAGEMENT_INTAKE.md), and
[Tax Return Lifecycle](TAX_RETURN_LIFECYCLE.md).
