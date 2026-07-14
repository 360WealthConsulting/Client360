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

### Sprint 4.4 — Client Portal and Secure Collaboration 🚧

- Portal identities, invitations, MFA, and delegated household access
- Secure messaging and document requests
- Client-facing workflow tasks, approvals, and status visibility
- E-signature and notification provider adapters
- Consent, communication preferences, retention, and portal audit controls

### Later Epic 4 sprints

- Tax return and TaxDome-replacement operations
- Client service and communication workflows
- Billing, revenue pipeline, and management reporting
- Compliance supervision, retention, and operational audit workflows

## Future intelligence and integrations

- Tax Intelligence: Drake, TaxDome migration, IRS notices, and planning data
- Additional custodians and live Schwab acquisition adapter
- QuickBooks and revenue intelligence
- AssetMark and remaining historical import sources
- AI meeting preparation, client briefs, and relationship-aware recommendations
- Client portal and secure document exchange
