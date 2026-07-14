# Operational Work Management Platform

## Purpose

Sprint 4.2 makes Client360 the daily operating workspace for advisors,
operations, tax, compliance, and management. It extends the Release 0.9
identity and record-security foundation without adding assignee columns to each
domain model.

## Assignment architecture

`record_assignments` remains the canonical generic edge between an entity and a
user or team. Supported entity types include people, households, tasks,
documents, workflow instances, workflow steps, and reserved future tax-return
and investment-account types. Assignment roles are primary, secondary,
supervisor, and owner. `work_assignment_details` stores provenance and reason;
`assignment_events` is an immutable history. Team-only ownership is supported
without a placeholder user.

Assignment create, reassign, and remove operations publish Client360 timeline
events when the entity resolves to a person or household. They also write
immutable security audit records. Automatic rules are deterministic, ordered
by priority, and match exact declared attributes.

## Workflow architecture

`workflow_instances` link reusable operational processes to a person or
household. Ordered `workflow_steps` carry status, priority, due date, SLA,
estimated effort, waiting-on state, blocked reason, and approval requirement.
Tax and investment workflow types can use this model before dedicated domain
records are introduced.

## Queue architecture

Queues are stored definitions, not copied records. Each definition contains
explainable JSON criteria evaluated against the caller's already-authorized
work items. Seeded queues cover waiting parties, review, delivery, high
priority, compliance, overdue, blocked, and unassigned work. The same engine
evaluates tasks and workflow steps and is extensible to documents and future
domain records.

## Dashboard architecture

`/work` presents the caller's agenda, assignments, households, document review,
approvals, upcoming meetings, overdue items, SLA risks, queues, capacity, and
bottlenecks. `/work/team` exposes authorized team metrics to callers with
`capacity.read`. Advisor, Operations, Tax, and Management panels use only
existing work types. Revenue pipeline is explicitly unavailable until a
revenue domain exists.

## Deterministic scoring

Priority score combines a documented priority weight, due-date proximity, SLA
risk, and blocked status. SLA risk is `healthy`, `warning` (24 hours),
`critical` (8 hours), or `breached`. Capacity is estimated committed minutes
divided by available minutes, defaulting to 480 per day. Bottlenecks group work
by waiting party or blocked status. Every output includes its component values
and can later be consumed by AI without making the AI the source of truth.

## Authorization

- `work.read`: authorized personal dashboards, queues, and APIs.
- `work.write`: protected assignment and work mutations.
- `capacity.read`: team workload and capacity.
- Existing `record.read_all` remains the explicit firm-wide bypass.

Non-privileged collection queries are filtered to direct user assignments,
active team memberships, and authorized person/household relationships. The UI
and API call the same filtered services. Mutation routes also require same-site
requests through the Release 0.9 middleware.

## API contracts

Versioned endpoints under `/api/v1/work` provide:

- `GET /my-work`, `/team-work`, `/dashboard-metrics`, `/daily-agenda`
- `GET /queues`, `/queues/{code}`, `/capacity`, `/assignments`
- `POST /assignments`, `/assignments/{id}/reassign`, `/assignments/automatic`
- `DELETE /assignments/{id}`

Create and reassign payloads identify the entity, assignment role, user/team,
and optional reason. Responses use stable entity identifiers and are suitable
for the current Jinja UI or future React/mobile clients.

## Manual testing

1. Sign in as an administrator and create primary, secondary, team, and
   supervisor assignments through the versioned API.
2. Confirm assignment events appear on the associated client timeline and in
   the audit log.
3. Sign in as an assigned advisor and verify `/work` contains only authorized
   records; verify an unassigned record is absent.
4. Verify queue counts match queue detail pages for overdue, blocked,
   waiting-on-client, and unassigned examples.
5. Sign in as Operations or an administrator and validate Team Work capacity
   and bottleneck panels.
6. Reassign and remove work; confirm history remains append-only and prior
   assignments become inactive.
7. Test empty filters, no-result queues, unauthorized access, invalid payloads,
   and mobile-width layout.

## Known extension points

Dedicated tax returns, investment account operations, and revenue pipeline
records are not fabricated in Sprint 4.2. Their entity types and dashboard
hooks are reserved, and later migrations can add domain-specific tables without
changing assignment, queue, audit, or API semantics.
