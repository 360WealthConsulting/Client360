# Practice Management Governance (Phase D.49)

`app/services/practice_management/governance.py` is a read-only checker that verifies the Practice Management
layer stays a **composition** over the authoritative operational owners and never becomes a second workflow
engine, scheduler, staffing/assignment engine, work queue, capacity/planning engine, metrics registry, or
persistence store. It returns `{ok, issue_count, findings}` and **never raises** into normal use.
`validate_practice_management()` is surfaced through the internal diagnostics endpoint
(`/practice/diagnostics`, gated by `observability.audit`).

## Enforced invariants

1. **No persistence / no mutation.** No module defines a `Table(...)`, writes the DB (`.insert(` / `.update(`
   / `.delete(` / `sa.insert` …), opens `engine.begin(`, publishes to the outbox (`publish_safe` /
   `publisher.publish`), or writes audit events (`write_audit_event`). No `rm_*` projection table is read
   directly.
2. **No duplicate engine.** No module calls an authoritative-owner **mutation** — `assign_work(`,
   `reassign_approval(`, `launch_workflow(`, `advance_workflow(`, `decide_approval(`, `request_approval(`,
   `create_capacity_plan(`, `update_capacity_plan(`, `book_meeting(`, `schedule_meeting(`, `create_meeting(`.
   The layer composes **reads** only.
3. **Composes the authoritative owners.** The engine + panels reference the authoritative reads
   (`operations.capacity`, `work_queue`, `workflow_automation`, `tax_domain`, `opportunity`, `analytics`),
   and the capacity owner (`operations.capacity`) specifically — so utilization is never recomputed.
4. **No second metrics registry.** No module defines a `_DEFS` catalog or a `Metric` class; the layer's
   counters register into the single Analytics Registry.
5. **Registry completeness + single ownership.** Every capacity model declares owner + governing workflow +
   workload source + utilization method + planning horizon + runtime gate + deep links; every resource
   declares all seven authoritative sources; every dashboard declares owner + audience + runtime gate +
   navigation + panels + required capabilities + governing services, and references only registered panels;
   every panel declares owner + source + deep link + explainability + permission; all registry keys are
   unique.
6. **Explainability enforced.** `is_explainable` (explanation + source + deep link) is a hard emit gate in
   both `model.py` and `panels.py`; a non-explainable panel is never emitted.
7. **No raw environment gating.** Gates flow through the Runtime Engine (`runtime.consumption.feature_enabled`)
   and policy through the Policy Engine — never `os.getenv` / `os.environ`.

## Authorization & least privilege

- Practice routes are gated by `capacity.read`; diagnostics by `observability.audit`.
- A dashboard is composed only if the principal holds one of its `required_capabilities` (`capacity.read`);
  otherwise `compose_dashboard` returns `None` (→ 404) and an authorization-failure counter increments.
- Each **panel self-restricts** to its own permission (`capacity.read` / `work.read` / `analytics.view`): a
  principal lacking it receives a `restricted` panel with `value = None` — the value is never leaked.
- All composed reads inherit the record scope + capability checks of their authoritative owner (book-scoped
  work queue, `operations.view` capacity, etc.).

## AI Assist boundary

AI Assist may **summarize** utilization, staffing, workload, and bottleneck **counts** (fact class `DERIVED`,
counts only, deep links only). It **never** assigns work, rebalances staff, modifies staffing, creates work
items, changes schedules, or invents a workload figure — every fact comes from a composed panel/section.

## Enforcement

`tests/test_practice_management.py` exercises the registries, explainable composition, authorization (`None`
+ restricted), gate/policy behavior, the analytics-counter reuse, diagnostics, the routes (registered +
capability-gated), and the architecture invariants (no mutation, no second engine, utilization from
`operations.capacity`, every panel deep-links). Route count, section registries, ADR count, and the single
migration head are guarded by `tests/test_platform_architecture.py`, `tests/test_client360_workspace.py`,
`tests/test_household360_workspace.py`, `tests/test_architecture_decision_records.py`, and the manifest.

See [PRACTICE_MANAGEMENT.md](PRACTICE_MANAGEMENT.md), [CAPACITY_PLANNING.md](CAPACITY_PLANNING.md),
[RESOURCE_REGISTRY.md](RESOURCE_REGISTRY.md), and [ADR-054](adr/ADR-054-practice-management.md).
