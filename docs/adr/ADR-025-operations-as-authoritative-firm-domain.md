# ADR-025 — Enterprise Operations as an authoritative firm-operations domain

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Domain Owner (Operations); Business Operations Owner (Michael Shelton — firm
project & capacity requirements); Compliance Architecture (operations audit history is regulated
recordkeeping). Authorized compliance reviewer: Not yet designated.

## Context
The platform had two client-anchored "task-like" systems and **no firm-operations domain**. The
`tasks` table has `person_id` **NOT NULL** (a task cannot exist without a client) and its service is
explicitly "a thin service, not a parallel task system." Advisor Work (`advisor_work_items`) is
recommendation-governed client work (one open item per `recommendation_id`/`governing_rule`).
Neither can represent a firm operational task with **no client** — "migrate the server", "onboard an
employee", "run RIA audit". There were no projects, phases, milestones, or persisted capacity/
workload tables (capacity was computed on the fly via `work_intelligence.capacity_metrics`). The
`operations` **role** existed but held only generic client/task/document capabilities; no
`operations.*`/`project.*` capability existed.

## Decision
Enterprise Operations becomes its **own authoritative domain** that owns **firm operational
metadata only** and is **never a source of truth for business records**. **Advisor Work remains the
authoritative client-work domain; the `tasks` table remains the authoritative client-task store.**
- **Owns:** `projects`, `project_phases`, `project_milestones`, `operational_tasks`,
  `operational_task_dependencies`, `operational_checklist_items`, `operational_resources`,
  `capacity_plans`, `operational_issues` (risk/issue), `operational_comments`, `project_templates`,
  and `operations_events` (an **append-only** audit ledger, trigger-blocked, polymorphic).
- **References, never owns:** every client/business link is an **optional** `ON DELETE SET NULL`
  reference — person/household/organization (`relationship_entities.id`), `opportunity_id`,
  `compliance_review_id`, `conversation_id` (Communications), `workflow_instance_id` (Workflow),
  `advisor_work_item_id` (Advisor Work), `meeting_id` (Scheduling), `document_id` (Documents). Firm
  work has **no** client anchor; a client link is incidental, not ownership.
- **Deterministic.** Lifecycle is a state machine (planned → active → completed, plus
  blocked/on_hold/cancelled/archived). Dependencies are finish-to-start gates on activation; cycles
  are rejected. **Capacity/workload/utilization are computed by plain arithmetic** (committed
  estimate minutes ÷ declared per-day capacity) — **no optimization engine, no AI recommendation.**
- **Workflow may CREATE operational tasks** via a new `create_operational_task` action in the
  workflow action registry (which invokes the Operations service) — Operations never owns Workflow.
- **Timeline:** approved lifecycle events only (`operations_project_created` / `_completed`,
  `operations_task_completed`, `operations_milestone_reached`) via `add_timeline_event`. The shared
  timeline **requires** a person/household anchor, so firm-only items **skip** publication (guarded,
  as in D.19) — `source="operations"`. **Analytics:** consumes firm-level `active_projects` /
  `open_operational_tasks` statistics (the no-book-scope pattern); Operations never depends on
  Analytics. **Microsoft 365** has no Planner/To-Do/Project surface — Operations is internal.
- **Security:** the `operations.view/manage/templates/audit*/admin*` capability family (`*` =
  sensitive) gates a new `/operations` surface (in-route; the prefix matches no middleware RULE).
  Operational tasks are routed under `/operations/.../items` (never `/tasks`) so they cannot collide
  with the unanchored client-`/tasks` middleware rule. Record scope is enforced in-service: a
  firm-level item (no anchor) is visible to `operations.view`; a client-anchored item additionally
  requires record scope.

## Alternatives considered
1. **Extend the `tasks` table for firm tasks.** Rejected: `person_id` is NOT NULL and the client
   task dashboard INNER JOINs `people`; making it nullable would corrupt the client-task invariant
   and every person-scoped read.
2. **Extend Advisor Work for operational work.** Rejected: Advisor Work is recommendation-governed
   client work (one-open-per-recommendation); firm chores have no recommendation and would break its
   invariants and blur the client-work boundary (ADR-007).
3. **Reuse `record_assignments`/`work_management` as the task engine.** Rejected as the primary
   store: that is the canonical *assignment* layer, not a project/task model; Operations uses direct
   assignment columns and may register its entity types there in future.
4. **Persist workload/utilization as materialized tables.** Rejected: they are deterministic
   functions of open tasks + declared capacity; computing them avoids drift (ADR-014/ADR-020).

## Reasons for the decision
The firm needs one authoritative model of *what firm-level work exists, how it is planned into
projects/phases/milestones, who is staffed to it, and whether the firm is over capacity* — none of
which the client-anchored systems can express. A new owned domain that references (never owns) every
other domain delivers this while keeping Advisor Work authoritative for client work and preserving
every ADR and the D.5 golden.

## Consequences
### Positive consequences
- A single authoritative firm-operations domain: projects, phases, milestones, operational tasks,
  dependencies, checklists, resources, capacity plans, deterministic workload/utilization, issues,
  and an append-only audit ledger.
- Advisor Work and the client `tasks` table are untouched and remain authoritative for client work.
- Workflow can now create operational tasks through the documented action seam; Analytics gains
  firm-level operations metrics; the timeline receives only approved, client-anchored lifecycle
  events.

### Negative consequences and tradeoffs
- A third "task-like" surface now exists (client `tasks`, Advisor Work, operational tasks) — a
  documented separation by ownership (client task vs. governed client work vs. firm work).
- Capacity/utilization are point-in-time computations from open estimates, not a scheduling
  optimizer; they inform, they do not allocate.
- Firm-only projects/tasks never appear on the client timeline (by design — the timeline is
  client-anchored); their history lives in `operations_events`.
- A project/task with audit events cannot be hard-deleted through the ledger; the ledger is
  append-only and polymorphic (no FK), so parent deletes leave audit rows as leftovers.

## Enforcement
- `app/database/operations_tables.py::define_operations_tables` (registered in
  `app/database/schema.py`; reflected in `app/db.py`). Migration `r8c9d0e1f2a3` (12 tables +
  append-only trigger on `operations_events` + 5 `operations.*` capabilities + 10 project
  templates). Services `app/services/operations/{common,projects,tasks,capacity,templates}.py`;
  routes `app/routes/operations.py` (in-route `operations.*` gating; `/operations/.../items` avoids
  the `/tasks` rule). Workflow action `create_operational_task` in
  `app/services/workflow_orchestration/actions.py`. The `tasks` table, Advisor Work, the D.5 golden,
  Scheduling, Communications, and the timeline projection are untouched. Tests:
  `tests/test_operations.py`; manifest / platform-architecture / route-count guards updated.

## Exceptions
None currently approved.

## Revisit conditions
A scheduling/allocation optimizer, a Microsoft Planner/Project/To-Do sync, materialized
workload/utilization snapshots, or making Operations authoritative for any client record would each
warrant a new or superseding ADR.

## References
- `app/services/operations/`, `app/routes/operations.py`, `app/database/operations_tables.py`,
  migration `migrations/versions/r8c9d0e1f2a3_operations_platform.py`
- Referenced domains: `app/services/advisor_work.py` (authoritative client work),
  `app/services/tasks.py` (authoritative client tasks), `app/services/scheduling/`,
  `app/services/communications/`, `app/services/workflow_orchestration/actions.py`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_operations.py`; relates to ADR-002, ADR-007, ADR-009, ADR-013, ADR-016, ADR-022,
  ADR-024
