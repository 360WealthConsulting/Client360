# Enterprise Capacity Planning, Workforce Operations & Resource Intelligence (Phase D.61)

`app/services/capacity_planning/` is a governed, **read-only composition** that provides a unified, governed
view of firm workforce operations, capacity, and utilization — staffing summaries, workload, queue health,
utilization, capacity forecasts, assignment distribution, and operational / advisor / automation workload. It
is **not** a second HR platform, HCM, scheduling application, calendar system, project-management system, PSA,
time-tracking platform, payroll platform, or workforce-management system: **no new capability, no new metric,
no persistence, no mutation, no duplicated workforce data, no migration** (single Alembic head `n5s6u7p8v9w0`).

> These are **operational summaries**, never certified staffing / utilization figures and never HR records.

## What it composes (existing owners only)

| Signal | Authoritative owner | Composed read | Capability |
| --- | --- | --- | --- |
| Firm utilization / capacity plans | Operations capacity owner | `operations.capacity.capacity_overview` / `list_capacity_plans` | `capacity.read` |
| Staffing recommendations | Practice Management (D.49) | `practice_summary` | `capacity.read` |
| Workload / backlog / queue health / assignments | Work Queue (D.39) | `work_queue.summary` (`unassigned_team` / `sla_breaches` / `my_overdue` / `by_domain`) | `work.read` |
| Automation-worker workload | Automation Orchestration (D.51) | `automation_summary` | `automation.view` |
| Servicing team (record-scoped) | authorization owner | `object_security.resolve_assignments` | record scope |

## The not_configured domains (reported honestly)

The D.61 audit confirmed several domains have **no authoritative owner** and are declared `not_configured` (the
D.55–D.60 precedent), never fabricated: **a full HR employee directory + contractors, PTO / availability,
time-tracking / time entries, payroll, and meeting / onboarding / planning capacity**. The Operations capacity
owner provides resource counts (not an HR directory), so the layer composes counts only — never employee
details.

## Registries, panels, dashboards

Three declarative registries — Workforce (8) + Capacity (9) + Utilization (5) — plus 23 panels and 8 dashboards
(workforce_overview, capacity_planning, advisor_utilization, operations_utilization, queue_health,
staffing_readiness, resource_allocation, executive_workforce_status). See
[WORKFORCE_REGISTRY.md](WORKFORCE_REGISTRY.md), [CAPACITY_REGISTRY.md](CAPACITY_REGISTRY.md), and
[UTILIZATION_REGISTRY.md](UTILIZATION_REGISTRY.md). Every dashboard carries a generated timestamp, governing
services, source inventory, explainable panels, deep links, and its configured / not_configured domain lists.

## Panels — counts, status, coverage only

Panels carry counts, status, and coverage only. They **never** return employee details, payroll, HR records,
calendar contents, time entries, or sensitive staffing data. The `executive_workforce_status` panel is a
DERIVED operational summary (labeled `derived`) — never a certified staffing / utilization figure.

## Authorization

- Routes + dashboards admit **operations OR an executive** (`capacity.read` / `analytics.executive`, via
  `require_any_capability`); diagnostics by `observability.audit`.
- Each **panel self-restricts** to its authoritative-source capability (workload / queue panels `work.read`,
  automation panels `automation.view`, executive panel `analytics.executive`). A principal lacking the panel
  capability receives a `restricted` panel with `value = None`, no hidden count, and no leaking metadata.
- Client-scoped sections compose ONLY the record-scoped `resolve_assignments` read — employee workload and firm
  utilization are never exposed at client/household scope.

## Runtime, governance, analytics, observability

Every surface is gated through the Runtime Engine (`capacity.enabled`, `workforce.enabled`,
`resource_intelligence.enabled`, `capacity_ai_summary.enabled`) **and** the runtime gate of every composed
source, plus the Policy Engine — **no environment bypass**. Governance (`validate_capacity_planning()`) returns
`{ok, issue_count, findings}` and forbids persistence, mutation, any staffing/scheduling/assignment/capacity
mutation, a second metrics registry, and a fabricated staffing / utilization figure — see
[RESOURCE_INTELLIGENCE_GOVERNANCE.md](RESOURCE_INTELLIGENCE_GOVERNANCE.md). Four low-cardinality counters
register into the **single** Analytics Registry. Internal diagnostics (`/capacity-planning/diagnostics`,
`observability.audit`) report registry coverage, configured vs not_configured counts, staffing / utilization
coverage, panel availability, and the governance summary.

## Surfaces

- **Advisor Workspace** — a **Capacity & Workload** panel (`ws["capacity_workload"]`) showing assigned
  workload, queue status, utilization summary, and staffing advisories.
- **Client 360 / Household 360** — a **Servicing Team** section (`capacity.read`): only the record-scoped
  staffing directly related to servicing that client / household (who is assigned to the record), via
  `resolve_assignments`. Employee workload, firm utilization, and unrelated staffing data are never exposed.
- **Executive Dashboard** — an **Enterprise Workforce & Capacity** dashboard reusing existing widgets
  (`advisor_workload`, `operational_health` — no new widget).
- **AI Assist** — summarizes workload / utilization / staffing / capacity / queue health. It **never** assigns
  work, approves staffing, schedules employees, modifies assignments, infers availability, or fabricates
  utilization.

## Routes

`/capacity-planning` (HTML) + `/api/v1/capacity-planning/{dashboards, dashboard/{key}, summary, registry,
panel/{key}, metrics}` + `/capacity-planning/diagnostics`.

See [WORKFORCE_REGISTRY.md](WORKFORCE_REGISTRY.md), [CAPACITY_REGISTRY.md](CAPACITY_REGISTRY.md),
[UTILIZATION_REGISTRY.md](UTILIZATION_REGISTRY.md),
[RESOURCE_INTELLIGENCE_GOVERNANCE.md](RESOURCE_INTELLIGENCE_GOVERNANCE.md), and
[ADR-066](adr/ADR-066-capacity-planning.md).
