# Practice Management (Phase D.49)

The **Practice Management** layer (`app/services/practice_management/`) is a governed, **read-only
composition** that gives firm operations and practice leadership one view of capacity, utilization, staffing,
workload, backlog, workflow aging, seasonal forecasts, and service-level performance — **without** building a
second workflow engine, scheduler, staffing/assignment engine, work queue, capacity/planning engine, or
metrics registry. Every number is composed on read from an **authoritative owner**; the layer owns no
persistence, defines no new metric, and never mutates, assigns, rebalances, or reschedules.

## What it composes (and never duplicates)

| Concern | Authoritative owner (composed) |
| --- | --- |
| Capacity / utilization | `app/services/operations/capacity.py` (D.20) — `capacity_overview`, `resource_utilization`, `over_capacity_count` |
| Workload / backlog / SLA | `app/services/work_queue/` — `compose_queue(filters=…)`, `work_queue_summary` |
| Workflow aging | `app/services/workflow_automation.py` — `workflow_metrics()` |
| Assignment | `app/services/work_management.py` — `assign_work` (**referenced, never called**) |
| Scheduling / availability | `app/services/scheduling/` (**referenced, never called**) |
| Advisor overload | `app/services/opportunity/intelligence.py`, `app/services/analytics/intelligence.py` |
| Tax / seasonal workload | `app/services/tax_domain.py` — `dashboard(principal)` |

See [CAPACITY_PLANNING.md](CAPACITY_PLANNING.md) for the capacity models, [RESOURCE_REGISTRY.md](RESOURCE_REGISTRY.md)
for the resource classes, and [PRACTICE_GOVERNANCE.md](PRACTICE_GOVERNANCE.md) for the enforced invariants.

## Modules

- `registry.py` — the four declarative catalogs: `CAPACITY_REGISTRY` (9 capacity models), `RESOURCE_REGISTRY`
  (6 resource classes), `PANEL_REGISTRY` (19 panels), `PRACTICE_DASHBOARDS` (8 dashboards).
- `model.py` — `PanelResult` + `PracticeDashboard` read-models. A panel is emitted only if
  `is_explainable` (explanation + source + deep link).
- `panels.py` — the per-panel compute functions. Read-only, fail-closed, and **self-restricting**: a
  principal lacking the panel's capability is shown a `restricted` panel, never its value.
- `service.py` — the engine: `compose_dashboard`, `list_dashboards`, `get_panel`, `practice_summary`,
  `client_workload`, `household_workload`.
- `gate.py` — runtime gates (`practice_management.enabled`, `capacity.enabled`, `staffing.enabled`) + policy
  composition. No raw environment gating.
- `stats.py` / `metrics.py` — low-cardinality in-process counters, registered into the **single** Analytics
  Registry (`analytics.metrics`). No second metrics registry.
- `diagnostics.py` — internal-only observability (`observability.audit`).
- `governance.py` — read-only invariant checker (never raises).

## Dashboards

`advisor_utilization`, `department_utilization`, `staffing`, `workload`, `backlog`, `workflow_aging`,
`seasonal_forecast`, `service_level`. Each carries a generated timestamp, governing services, source
inventory, explainable panels, and deep links to the authoritative surface. Dashboards are gated by
`capacity.read`; each panel additionally self-restricts to its own capability (`capacity.read` / `work.read`
/ `analytics.view`).

## Surfaces

- **HTTP** (`app/routes/practice_management.py`, gated by `capacity.read`; diagnostics by
  `observability.audit`): `/practice` (HTML), `/api/v1/practice/dashboards`, `/api/v1/practice/dashboard/{key}`,
  `/api/v1/practice/summary`, `/api/v1/practice/registry`, `/api/v1/practice/panel/{key}`,
  `/api/v1/practice/metrics`, `/practice/diagnostics`.
- **Advisor Workspace** — the Capacity Planning panel (`practice_summary`).
- **Client 360 / Household 360** — the `operational_workload` section (`client_workload` /
  `household_workload`, book-scoped work-queue rollups; household is a count rollup, never a re-sum of
  incompatible member units).
- **Executive Dashboard** — a `practice_management` dashboard (composed from existing D.48 widgets; no new
  widget).
- **AI Assist** — summarizes operational-workload counts only; it never assigns work, rebalances staff,
  changes schedules, or invents a figure.

## Invariants

No new persistence, no new metric, no new capability, no migration (single Alembic head unchanged). No
mutation, no outbox publication, no audit write, no second engine. Every utilization figure comes from
`operations.capacity`; every dashboard panel is explainable and deep-links to its authoritative surface.
Enforced by `app/services/practice_management/governance.py` and `tests/test_practice_management.py`. See
[ADR-054](adr/ADR-054-practice-management.md).
