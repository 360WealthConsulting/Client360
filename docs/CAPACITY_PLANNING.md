# Capacity Planning (Phase D.49)

Capacity planning in Client360 is **deterministic and composed**, never optimized or AI-driven. The
authoritative capacity/utilization owner is **Operations Capacity** (`app/services/operations/capacity.py`,
Phase D.20): capacity plans are persisted per-resource, per-period allocation records, and utilization is
computed as `committed_minutes / declared_capacity` from open operational tasks — plain arithmetic, no
optimization engine, no recommendation model.

The Practice Management layer (D.49) **composes** that owner into named capacity models. It computes no new
capacity numbers; it references the owner and explains the result with a deep link.

## Capacity model registry (`CAPACITY_REGISTRY`)

Each model declares its `owner` (always `operations.capacity` — the single capacity owner), `governing
workflow`, `workload_source`, `utilization_method`, `planning_horizon`, `runtime_gate`, `refresh_policy`, and
`deep_links`.

| Model | Governing workflow | Workload source | Planning horizon |
| --- | --- | --- | --- |
| `advisor_capacity` | advisor_work | `work_queue.compose_queue` | weekly |
| `tax_preparation` | tax_engagement | `tax_domain.dashboard` | seasonal |
| `insurance_servicing` | insurance_case | `work_queue.compose_queue` | weekly |
| `investment_operations` | operational_task | `operations.capacity.capacity_overview` | weekly |
| `compliance_reviewers` | compliance_review | `compliance_intelligence.supervisory_dashboard` | weekly |
| `administrative_staff` | operational_task | `operations.capacity.capacity_overview` | weekly |
| `onboarding` | client_onboarding | `workflow_automation.workflow_metrics` | monthly |
| `client_service` | advisor_work | `work_queue.compose_queue` | weekly |
| `seasonal_workload` | tax_engagement | `tax_domain.dashboard` | seasonal |

Every `utilization_method` is deterministic (a committed-vs-declared ratio or a plain count against capacity)
— there is no optimization, forecasting model, or AI.

## Utilization method

For minute-based resources, utilization is `operations.capacity.resource_utilization`:
`committed_minutes / available_minutes` (available defaults to the resource's declared
`capacity_minutes_per_day`), with `over_capacity = committed > available`. The firm rollup
(`capacity_overview`) reports `resource_count`, `over_capacity_count`, and per-resource utilization. For
count-based resources (tax returns, compliance reviews, onboarding workflows), utilization is open items vs a
declared capacity count from the authoritative domain read.

## Capacity dashboards

The capacity/utilization dashboards are `advisor_utilization`, `department_utilization`, and
`seasonal_forecast` (plus the `staffing` dashboard's over-capacity + advisory signals). Each panel:

- **`firm_capacity_utilization`** — resource count, over-capacity count, and average utilization from
  `capacity_overview`.
- **`department_capacity`** — per-resource utilization grouped for the department view.
- **`over_capacity_resources`** — resources over declared capacity (name, utilization, committed vs available
  minutes).
- **`capacity_horizon`** — registered capacity plans + capacity models by planning horizon.
- **`seasonal_tax_forecast`** — tax returns due within the planning horizon + overdue.

Each panel is explainable (explanation + `operations.capacity`/tax source + deep link to
`/operations/capacity` or `/tax`) and gated by `capacity.read`; a principal lacking it sees a `restricted`
panel, never a value.

## What capacity planning is NOT

- **Not** a second capacity engine — `operations.capacity` owns every utilization figure.
- **Not** an optimizer or scheduler — the layer never books time or reassigns work.
- **Not** persisted — dashboards are recomputed per request from the authoritative reads. Durable capacity
  plans live in Operations Capacity.

See [RESOURCE_REGISTRY.md](RESOURCE_REGISTRY.md), [PRACTICE_MANAGEMENT.md](PRACTICE_MANAGEMENT.md), and
[ADR-054](adr/ADR-054-practice-management.md).
