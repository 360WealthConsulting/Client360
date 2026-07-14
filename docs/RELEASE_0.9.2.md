# Client360 Release 0.9.2 — Workflow and Process Automation

Release date: July 14, 2026

## Overview

Release 0.9.2 delivers Sprint 4.3 of the Epic 4 Practice Management Platform.
Client360 can now launch, govern, execute, approve, escalate, and report on
repeatable firm processes while preserving immutable version and execution
history.

## Features delivered

- Immutable templates identified by stable code and version.
- Launch-time template and step-definition snapshots.
- Directed dependencies with recursive circular-dependency prevention.
- Sequential, parallel, and declarative conditional execution.
- Pause, resume, cancel, complete, and reopen lifecycle controls.
- Independent approval routing with service and database segregation of duties.
- SLA deadlines, idempotent escalations, and five-minute scheduler evaluation.
- Event-to-template triggers and an idempotent internal action ledger.
- Assignment, queue, timeline, audit, document, relationship, portfolio, and
  Microsoft-event integration through vendor-independent entity/event envelopes.
- Workflow list and instance-detail pages plus metrics and reporting data.

## Database and migrations

- Schema version: Release 0.9.2
- Alembic head: `e530f5b3d4e5`
- Parent: Release 0.9.1 head `d420f4a2c3d4`
- Application tables: 59
- New tables: `workflow_templates`, `workflow_template_steps`,
  `workflow_step_dependencies`, `workflow_events`, `automation_triggers`,
  `automation_actions`, and `workflow_escalations`
- Extended tables: `workflow_instances`, `workflow_steps`, and `work_approvals`
- Migration lineage: one head

## APIs added

Versioned `/api/v1/workflows` APIs provide template discovery, workflow launch
and detail, pause, resume, cancel, complete, reopen, step completion, approval
requests and decisions, domain-event processing, SLA evaluation, and metrics.

## Seeded workflow templates

Prospecting, client onboarding, Schwab account opening, asset transfer, annual
review, tax preparation, tax extension, IRS notice, estate planning, insurance
review, client termination, and compliance review are published at version 1.

## Validation

- 50 automated tests passed.
- Python compilation and FastAPI lifespan startup passed.
- 104 application routes registered; OpenAPI and Jinja rendering passed.
- Empty-database base-to-head migration passed.
- Upgrade from Release 0.9.1 passed.
- Downgrade to Release 0.9.1 and re-upgrade passed.
- Sentinel client, assignment, task, document, and legacy workflow data survived.
- Exactly one Alembic head remained.

See `SPRINT_4_3_RELEASE_VALIDATION.md` for the complete RC3 evidence.

## Known limitations

- Template authoring and publication do not yet have a user-facing editor.
- Conditional rules intentionally support deterministic equality matching only.
- External provider actions must be introduced through provider/domain adapters;
  only internal timeline publication is enabled today.
- Legacy Release 0.9.1 workflows have empty snapshot JSON because no historical
  published definitions exist to reconstruct.
- A dedicated scheduler leader should be considered before horizontal scaling.
- Production still requires the Release 1.0 OIDC/MFA, live Microsoft, Schwab,
  scale, backup/restore, accessibility, and observability readiness gates.

## Recommended Sprint 4.4

Client Portal and Secure Collaboration: portal identities and delegated access,
secure messaging, document requests, client-facing workflow tasks and approvals,
e-signature adapters, notifications, consent, retention, and portal audit.

Sprint 4.4 is not included in this release and has not started.
