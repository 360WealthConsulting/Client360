# Client360 Release 0.9.1 — Operational Work Management

Release date: July 14, 2026

## Overview

Release 0.9.1 delivers Sprint 4.2 of the Epic 4 Practice Management Platform.
Client360 now provides a secure daily operating workspace for assigned client,
task, document, workflow, approval, queue, capacity, and SLA work.

## Features delivered

- Generic assignments for people, households, tasks, documents, workflow
  instances, workflow steps, and reserved future tax and investment entities.
- Primary, secondary, supervisor, user, and team ownership with active periods,
  reassignment history, and deterministic automatic assignment rules.
- Immutable assignment events, immutable security audit records, and client or
  household timeline publication.
- Workflow instances and ordered workflow steps with due dates, SLA targets,
  estimated effort, waiting-on state, blocking, and approval requirements.
- My Work, Team Work, and queue dashboards with assignment controls, filters,
  empty states, daily priorities, document review, meetings, approvals,
  overdue work, SLA risk, workload, capacity, and bottlenecks.
- Advisor, Operations, Tax, and Management domain panels using existing data;
  unavailable revenue metrics remain explicit placeholders.
- Deterministic priority, capacity, SLA-risk, queue, daily-agenda, and
  bottleneck calculations.
- Versioned APIs under `/api/v1/work` for personal and team work, queues,
  assignments, reassignment, automatic rules, capacity, dashboard metrics, and
  daily agendas.
- Capability, team, assignment, and record-level filtering shared by UI and API.

## Database and migrations

- Schema version: Release 0.9.1
- Alembic head: `d420f4a2c3d4`
- Application tables: 52
- New tables: `workflow_instances`, `workflow_steps`, `assignment_rules`,
  `work_assignment_details`, `assignment_events`, `work_queues`, and
  `work_approvals`
- Migration lineage: one head
- Supported upgrade: Release 0.9 head `c410f4a1b2c3` → Release 0.9.1 head

## Validation

- 39 automated tests passed.
- Python compilation and application startup passed.
- 88 routes registered and 21 templates parsed.
- Clean database migration to head passed.
- Downgrade to Release 0.9 and re-upgrade passed with assignment data.
- Upgrade from a valid Release 0.9 database preserved sentinel client data.
- Assignment authorization, automatic rules, timeline, audit, immutable
  history, queues, scoring, and API contracts passed regression coverage.

## Documentation

- `WORK_MANAGEMENT_PLATFORM.md`
- `EPIC_4_PRACTICE_MANAGEMENT_PLATFORM.md`
- `ROADMAP.md`
- `README.md`

## Known limitations

- Tax returns, investment operations, and revenue pipeline remain extension
  hooks until their dedicated domain models exist.
- Capacity assumes 480 available minutes per day until staff schedules exist.
- Production OIDC/MFA, Microsoft tenant, Schwab export, production-scale
  migration, backup/restore, visual/accessibility, and operational monitoring
  checks from Release 0.9 remain deployment gates.
- The development runtime emits the existing urllib3 LibreSSL warning;
  production should use a supported OpenSSL runtime.

## Remaining roadmap

- Sprint 4.3 Workflow and Process Automation: reusable templates, step
  dependencies, approvals, escalations, automation triggers, and workflow
  execution controls.
- Tax-practice operations, client service workflows, billing and revenue
  pipeline, and compliance supervision.
- Release 1.0 operational-readiness gates and live integration validation.

Sprint 4.3 is not included and has not started.
