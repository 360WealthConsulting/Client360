# Client360 Roadmap

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
2. Engagement intake, organizers, questionnaires, and engagement letters 🚧
3. Return lifecycle and production-stage automation
4. Tax document intelligence and missing information
5. Extensions, estimates, notices, and amendments
6. Review, approval, e-file, delivery, and compliance
7. Secure tax portal and client collaboration
8. Drake/provider and IRS transcript integration
9. Production reporting, capacity, AI extensions, and release readiness

Sprint 5.1 shipped in Release v0.9.4. Sprint 5.2 is implemented in a draft
release candidate and has not been merged. See
[Epic 5 Technical Design](EPIC_5_TAX_PRACTICE_PLATFORM.md) and
[Tax Domain Foundation](TAX_DOMAIN_FOUNDATION.md), plus
[Tax Engagement Intake](TAX_ENGAGEMENT_INTAKE.md).
