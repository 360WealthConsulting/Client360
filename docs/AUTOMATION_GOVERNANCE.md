# Automation Orchestration Governance (Phase D.51)

`app/services/automation_orchestration/governance.py` is a read-only checker that verifies the Automation
Orchestration layer stays a **composition** over the authoritative operational services and never becomes a
second workflow engine, scheduler, rules engine, orchestration engine, event bus, or automation platform. It
returns `{ok, issue_count, findings}` and **never raises** into normal use.
`validate_automation_orchestration()` is surfaced through the internal diagnostics endpoint
(`/automation-orchestration/diagnostics`, gated by `observability.audit`).

## Enforced invariants

1. **No persistence / no mutation.** No module defines a `Table(...)`, writes the DB (`.insert(` / `.update(`
   / `.delete(` / `sa.insert` …), opens `engine.begin(`, publishes to the outbox (`publish_safe` /
   `publisher.publish`), or writes audit events (`write_audit_event`). No `rm_*` projection table is read
   directly.
2. **No duplicate workflow execution / scheduler / event routing.** No module calls a workflow / automation /
   trigger / event **execution** — `launch_workflow(`, `transition_workflow(`, `complete_step(`,
   `request_approval(`, `decide_approval(`, `reassign_approval(`, `process_event(`,
   `execute_automation_action(`, `evaluate_sla(`, `.fire(`, `execute_action(`, `configure_trigger(`,
   `enqueue_run(`, `execute_run(`, `run_job(`, `run_worker_cycle(`, `execute_dispatch(`, `publish_safe(`,
   `publish_event(`. The layer composes **reads** only.
3. **No second metrics registry.** No module defines a `_DEFS` catalog or a `Metric` class; the layer's
   counters register into the single Analytics Registry.
4. **Registry completeness + single ownership.** Every automation declares owner + workflow owner + trigger
   source + execution owner + scheduling owner + notification owner + runtime gate + deep links; every trigger
   type declares owner + source + execution owner + runtime gate; every action declares authoritative owner +
   execution service + permissions + runtime gate; every dashboard declares owner + audience + runtime gate +
   navigation + panels + required capabilities + governing services, and references only registered panels;
   every panel declares owner + source + deep link + explainability + permission; all registry keys are
   unique.
5. **Explainability enforced.** `is_explainable` (explanation + source + deep link) is a hard emit gate in
   both `model.py` and `panels.py`; a non-explainable panel is never emitted.
6. **No raw environment gating.** Gates flow through the Runtime Engine (`runtime.consumption.feature_enabled`)
   and policy through the Policy Engine — never `os.getenv` / `os.environ`.

## No workflow payloads, ever

Panels and summaries carry **counts + status only** — never workflow payloads, entity identifiers, or any
client-sensitive automation data. Diagnostics and analytics counters are low-cardinality aggregates about the
layer itself. This is a structural invariant of the model (`PanelResult` values are counts/status/rollups)
and of the compose layer (it reads engine *metrics* and *counts*, never a workflow payload).

## Authorization & least privilege

- Automation routes are gated by `automation.view`; diagnostics by `observability.audit`.
- A dashboard is composed only if the principal holds one of its `required_capabilities` (`automation.view`);
  otherwise `compose_dashboard` returns `None` (→ 404) and an authorization-failure counter increments.
- Each **panel self-restricts** to `automation.view`: a principal lacking it receives a `restricted` panel
  with `value = None` — never leaked.
- All composed reads inherit the record scope + capability checks of their authoritative owner (the Workflow
  Orchestration facade's scope clause, the Automation engine's run scope, Scheduling / Communications scope).

## AI Assist boundary

AI Assist may **summarize** automation counts (pending automations, execution status, failed automations,
workflow progress) — fact class `DERIVED`, counts only, deep links only. It **never** executes automations,
approves workflows, triggers events, bypasses policies, or alters workflow state — every fact comes from a
composed section/summary.

## Enforcement

`tests/test_automation_orchestration.py` exercises the registries, explainable composition, authorization
(`None` + restricted), gate/policy behavior, the analytics-counter reuse, diagnostics, the routes (registered
+ capability-gated), AI summarize-only, and the architecture invariants (no second workflow engine /
scheduler / event bus, no mutation, workflow reads composed from the Workflow Engine, every dashboard
deep-links, every execution summary names an authoritative owner). Route count, section registries, ADR
count, and the single migration head are guarded by `tests/test_platform_architecture.py`,
`tests/test_client360_workspace.py`, `tests/test_household360_workspace.py`,
`tests/test_architecture_decision_records.py`, and the manifest.

See [AUTOMATION_ORCHESTRATION.md](AUTOMATION_ORCHESTRATION.md), [AUTOMATION_REGISTRY.md](AUTOMATION_REGISTRY.md),
[TRIGGER_REGISTRY.md](TRIGGER_REGISTRY.md), and [ADR-056](adr/ADR-056-automation-orchestration.md).
