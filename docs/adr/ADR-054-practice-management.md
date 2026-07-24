# ADR-054 — Enterprise Practice Management & Capacity Planning: A Read-Only Composition, Not a Second Workflow/Scheduler/Staffing/Planning Engine

## Status
Accepted

## Date
2026-07-24

## Decision owners
Platform Architecture; Domain Owner (Firm Operations / Practice Leadership); Reliability / Operations;
Security / Authorization (RBAC ownership); Compliance; Business Operations Owner (Michael Shelton).

## Context
The mandatory D.49 audit found the platform already owns every workload, assignment, staffing, scheduling,
routing, utilization, capacity, and resource-management capability:

* **Operations Capacity** (`app/services/operations/capacity.py`, D.20) — the authoritative capacity /
  utilization owner: per-resource, per-period capacity plans plus deterministic `resource_workload`,
  `resource_utilization`, `capacity_overview(principal)`, and `over_capacity_count(principal)` computed from
  open operational tasks against declared per-day capacity (plain arithmetic — no optimization, no AI). Reads
  require `operations.view`.
* **Unified Work Queue** (`app/services/work_queue/`, D.39) — the authoritative cross-domain work surface:
  `compose_queue(principal, filters=…)` (per-advisor/per-team/per-person/per-household filters) +
  `work_queue_summary`.
* **Work Management / Assignment** (`app/services/work_management.py`) — the authoritative assignment owner
  (`assign_work`, assignment roles). **Workflow Automation** (`workflow_automation.workflow_metrics`) owns
  workflow state. **Scheduling** (`app/services/scheduling/`) owns availability + meetings. The opportunity +
  Analytics **firm-intelligence** layers own advisor-overload signals; the **tax domain** owns tax workload.

There was **no practice-management layer** unifying these into firm-wide advisor/department utilization,
staffing, workload, backlog, workflow-aging, seasonal-forecast, and service-level views. Building a second
workflow engine, scheduler, staffing/assignment engine, work queue, capacity/planning engine, or metrics
registry would violate the "no second system" invariant and duplicate governed, gated infrastructure.

## Decision
Phase D.49 adds a **governed, read-only practice-management composition layer**
(`app/services/practice_management/`) with NO new metrics, NO persistence, and NO mutation:

1. Four declarative **registries** (`registry.py`): `CAPACITY_REGISTRY` (9 capacity models — owner, governing
   workflow, workload source, utilization method, planning horizon, runtime gate, refresh policy, deep
   links), `RESOURCE_REGISTRY` (6 resource classes — capabilities + authoritative workload / assignment /
   scheduling / utilization / availability sources), `PANEL_REGISTRY` (19 panels), and `PRACTICE_DASHBOARDS`
   (8 dashboards). Every capacity model names `operations.capacity` as its owner — the layer computes nothing.
2. Normalized read-models (`model.py`): `PanelResult` + `PracticeDashboard`, each explainable (explanation +
   source + deep link, a hard emit gate) and reference-only.
3. A **panel compute layer** (`panels.py`): each panel's value is composed on read by its authoritative
   service (Operations Capacity, the Unified Work Queue, Workflow Automation, the opportunity + Analytics
   firm-intelligence layers, the tax domain). Fail-closed; every panel self-restricts to its own capability
   (a principal lacking it sees a `restricted` panel, never a value).
4. The **practice-management engine** (`service.py`): `compose_dashboard`, `list_dashboards`, `get_panel`,
   `practice_summary`, plus `client_workload` / `household_workload` (book-scoped work-queue rollups for
   Client 360 / Household 360). Every dashboard carries generated timestamp, governing services, source
   inventory, explainable panels, and deep links. Dashboard-level authorization (`capacity.read`).
5. **Runtime gates** (`practice_management.enabled` + `capacity.enabled` + `staffing.enabled`), **policy
   composition**, **analytics reuse** (four operational counters registered into the ONE Analytics Registry —
   no second registry), internal **diagnostics** (`observability.audit`), and a read-only **governance**
   checker that forbids mutation, persistence, and any call into an authoritative-owner mutation
   (`assign_work`, `launch_workflow`, `create_capacity_plan`, `book_meeting`, …). AI Assist may summarize
   utilization/staffing/workload counts but never assigns, rebalances, reschedules, or invents a figure.

No migration, no new table, no new capability (reuses `capacity.read` + `work.read` + `analytics.view` +
`operations.view` + `observability.audit`), no new metric, no new outbox contract. Single Alembic head stays
`n5s6u7p8v9w0`.

## Alternatives considered
- **A second workflow/scheduler/staffing/planning engine or work queue.** Rejected: Operations Capacity,
  Work Management, Workflow Automation, Scheduling, and the Unified Work Queue are the authoritative owners;
  D.49 composes them. Governance forbids duplicate engines, tables, and copied operational data.
- **A second metrics/capacity registry.** Rejected: utilization numbers come from `operations.capacity`
  (the one capacity owner) and counts from the authoritative reads; the layer registers only operational
  counters (about itself) into the single Analytics Registry — the house style.
- **Persisting composed capacity plans / staffing recommendations.** Rejected: they are a deterministic
  function of the authoritative data at read time; a store would be a planning warehouse to reconcile, and
  the staffing signals are advisory-only (the firm assigns via Work Management, never here).

## Reasons for the decision
Practice leadership needs one capacity/staffing view; the authoritative services already own every number
with the correct scoping. A read-only composition gives that view with full explainability (source + deep
link) while every utilization figure stays owned by Operations Capacity, every assignment by Work Management,
every workflow by Workflow Automation, and every schedule by Scheduling. Deep links (never inline mutation)
route the manager to the authoritative surface to act.

## Consequences

### Positive consequences
- One firm-wide practice-management surface with no second workflow/scheduler/staffing/queue/planning engine.
- Utilization is inherited from Operations Capacity (the D.20 owner) — one source of truth, deterministic.
- Zero schema change: no migration, table, capability, metric, or outbox contract.
- Advisor Workspace Capacity Planning panel + Client 360 / Household 360 operational-workload sections +
  an Executive Practice Management dashboard (reusing existing widgets) + AI summarize-only, all from one
  layer.

### Negative consequences and tradeoffs
- Dashboards are recomputed per request (no persistence) — bounded by the authoritative reads' cost.
- The layer's coverage is bounded by the authoritative owners; a genuinely new capacity signal is added to
  Operations Capacity first, then surfaces here.
- Staffing recommendations are advisory only — the layer never assigns or rebalances (by design).

## Enforcement
`tests/test_practice_management.py` (four registries + single ownership; explainable dashboard composition;
authorization — unauthorized → None, unentitled panel restricted never valued; runtime + policy gates; the
firm summary + client/household rollups; analytics reuse — the 4 counters in the ONE registry; diagnostics;
routes registered + capability-gated; and the architecture invariants — no Table / no `_DEFS` / no mutation /
no second workflow engine / no scheduler / no `assign_work` / utilization composed from `operations.capacity`).
`app/services/practice_management/governance.py` enforces the invariants at runtime. Route count, section
registries, and migration head are guarded by `tests/test_platform_architecture.py` +
`tests/test_client360_workspace.py` + `tests/test_household360_workspace.py` + the manifest.

## Exceptions
Firm-global reads that do not self-gate (Operations Capacity overview, workflow metrics) are exposed only
within dashboards whose required capability (`capacity.read`) the principal holds; each panel additionally
self-restricts to its own permission (`capacity.read` / `work.read` / `analytics.view`), so a value is never
shown to a principal lacking that capability.

## Revisit conditions
Revisit when a new capacity signal is required (add it to Operations Capacity), when durable capacity plans
or seasonal staffing models are needed (extend Operations Capacity's persisted plans, never a second store),
or if a materialized practice read-model is ever justified (it would be a governed projection, never a second
planning warehouse).

## References
- `app/services/practice_management/*` (`registry.py`, `model.py`, `service.py`, `panels.py`, `gate.py`,
  `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`, `__init__.py`)
- `app/routes/practice_management.py`; Client 360 section in
  `app/services/client360/{registry,sections}.py`; Household 360 section in
  `app/services/client360/household.py`; Capacity Planning panel in `app/services/workspace/service.py`;
  Executive Practice Management dashboard in `app/services/executive_intelligence/registry.py`; AI grounding
  in `app/services/ai_assist/context.py`; analytics counters in `app/services/analytics/{sources,metrics}.py`
- Composes `app/services/operations/capacity.py`, `app/services/work_queue/{service,summary}.py`,
  `app/services/workflow_automation.py`, `app/services/opportunity/intelligence.py`,
  `app/services/analytics/intelligence.py`, `app/services/tax_domain.py`
- `docs/PRACTICE_MANAGEMENT.md`, `docs/CAPACITY_PLANNING.md`, `docs/RESOURCE_REGISTRY.md`,
  `docs/PRACTICE_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_practice_management.py`; relates to ADR-015, ADR-020, ADR-021, ADR-025, ADR-046 through ADR-053
