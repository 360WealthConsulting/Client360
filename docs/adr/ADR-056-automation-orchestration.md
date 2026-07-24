# ADR-056 — Enterprise Automation Orchestration & Business Process Composition: A Read-Only Composition, Not a Second Workflow/Scheduler/Event Engine

## Status
Accepted

## Date
2026-07-24

## Decision owners
Platform Architecture; Domain Owner (Firm Operations / Business Process); Reliability / Operations; Security /
Authorization (RBAC ownership); Compliance; Business Operations Owner (Michael Shelton).

## Context
The mandatory D.51 audit found the platform already owns every automation, trigger, workflow-execution,
scheduling, notification, approval, orchestration, and event capability — across **three distinct engines**:

* **Workflow Engine** — `app/services/workflow_automation.py` (the low-level, firm-global engine:
  `workflow_metrics()`, `workflow_detail()`, plus every execution write — `launch_workflow`,
  `transition_workflow`, `request_approval`, `decide_approval`, `process_event`, `evaluate_sla`) and its
  principal-scoped facade `app/services/workflow_orchestration/service.py` (`list_instances(principal)`,
  `metrics(principal)`, `audit_history(principal)`, `templates()`). Triggers are owned by
  `workflow_orchestration/triggers.py` (`list_triggers` read; `fire`/`configure_trigger` write); the action
  catalog by `workflow_orchestration/actions.py` (`list_actions` read).
* **Automation engine** (scheduled jobs / runs, ADR-027) — `app/services/automation/service.py`
  (`metrics(principal)` → jobs/enabled_jobs/runs/running/failed/succeeded; `list_jobs`, `list_runs`); the
  scheduler tick is `automation/runner.py`.
* **Event outbox** — `app/services/events/` (`diagnostics.event_counts()`, `events_by_domain()`,
  `dead_letters()`; the transactional outbox `outbox_events`); **Scheduling** (`scheduling/service.metrics`)
  and **Communications** (`communications/service.metrics`) own scheduling + notification reads.

There was **no automation-orchestration layer** unifying these into named, firm-wide views of automation
inventory, workflow status, trigger activity, execution status, pending, and failed automations. Building a
second workflow engine, scheduler, rules engine, orchestration engine, event bus, or automation platform
would violate the "no second system" invariant and duplicate governed, gated infrastructure.

## Decision
Phase D.51 adds a **governed, read-only automation-orchestration composition layer**
(`app/services/automation_orchestration/`) with NO new metrics, NO persistence, and NO mutation:

1. Three declarative **registries** (`registry.py`): `AUTOMATION_REGISTRY` (9 business automations — owner,
   workflow owner, trigger source, execution owner, runtime gate, scheduling owner, notification owner, deep
   links), `TRIGGER_REGISTRY` (7 trigger types — owner, source, execution owner, runtime gate),
   `ACTION_REGISTRY` (6 actions — authoritative owner, execution service, permissions, runtime gate), plus
   `PANEL_REGISTRY` (17 panels) and `ORCHESTRATION_DASHBOARDS` (6 dashboards).
2. Normalized read-models (`model.py`): `PanelResult` + `OrchestrationDashboard`, each explainable
   (explanation + source + deep link, a hard emit gate) and reference-only; **counts + status only, never a
   workflow payload**.
3. A **panel compute layer** (`panels.py`): each panel's value is composed on read by its authoritative
   owner (the Workflow Engine, the Automation engine, the Trigger engine, the Event outbox, Scheduling,
   Communications). Fail-closed; every panel self-restricts to `automation.view`.
4. The **automation-orchestration engine** (`service.py`): `compose_dashboard`, `list_dashboards`,
   `get_panel`, `automation_summary`, plus `client_automation` / `household_automation` (record-scoped
   workflow-facade rollups). Every dashboard carries generated timestamp, governing services, source
   inventory, explainable panels, and deep links. Dashboard-level authorization (`automation.view`).
5. **Runtime gates** (`automation.enabled` + `orchestration.enabled` + `triggers.enabled`), **policy
   composition**, **analytics reuse** (four operational counters registered into the ONE Analytics Registry —
   no second registry), internal **diagnostics** (`observability.audit`), and a read-only **governance**
   checker that forbids mutation, persistence, and any call into a workflow / automation / trigger / event
   execution (`launch_workflow`, `decide_approval`, `process_event`, `.fire(`, `execute_action`,
   `enqueue_run`, `publish_safe`, …). AI Assist may summarize automation counts but never executes
   automations, approves workflows, triggers events, bypasses policies, or alters workflow state.

No migration, no new table, no new capability (reuses `automation.view` + `observability.audit`), no new
metric, no new outbox contract. Single Alembic head stays `n5s6u7p8v9w0`.

## Alternatives considered
- **A second workflow/scheduler/rules/orchestration engine or event bus.** Rejected: the Workflow Engine,
  the Automation engine, the Trigger engine, Scheduling, and the Event outbox are the authoritative owners;
  D.51 composes them. Governance forbids duplicate engines, tables, and event routing.
- **A second metrics registry.** Rejected: automation counts come from the authoritative engines'
  `metrics()`/`event_counts()` reads; the layer registers only operational counters (about itself) into the
  single Analytics Registry — the house style.
- **Persisting composed automation state.** Rejected: dashboards are a deterministic function of the
  authoritative data at read time; a store would be an automation warehouse to reconcile, and the layer must
  never hold automation state (that is the engines' job).

## Reasons for the decision
Operations leadership needs one automation view; the authoritative engines already own every number with the
correct scoping. A read-only composition gives that view with full explainability (source + deep link) while
every workflow stays owned by the Workflow Engine, every scheduled job by the Automation engine, every
trigger by the Trigger engine, every event by the outbox, and every schedule/notification by
Scheduling/Communications. Deep links (never inline execution) route the operator to the authoritative
surface to act. Emitting counts + status only keeps workflow payloads and client-sensitive automation data
out of the layer entirely.

## Rationale for avoiding a second workflow or orchestration engine
A second workflow/orchestration engine would require duplicated execution state, a parallel trigger + action
model, its own scheduler and event routing, and its own access model — duplicating governed, gated
infrastructure and creating reconciliation + drift + double-execution risk, with no benefit the composition
does not already provide. Composing over the single Workflow Engine + Automation engine keeps one source of
truth for every workflow and job, one place execution is gated, and zero duplicated state.

## Consequences

### Positive consequences
- One firm-wide automation surface with no second workflow engine, scheduler, event bus, or automation
  platform.
- Record scope + capability are inherited from the composed engine reads; a non-`automation.view` principal
  sees restricted panels, never values, and never a workflow payload.
- Zero schema change: no migration, table, capability, metric, or outbox contract.
- Advisor Workspace Automation Status panel + Client 360 / Household 360 Automation History sections + an
  Executive Automation dashboard (reusing existing widgets) + AI summarize-only, all from one layer.

### Negative consequences and tradeoffs
- Dashboards are recomputed per request (no persistence) — bounded by the authoritative reads' cost.
- Per-client / per-household automation rollups filter the in-scope workflow-facade page client-side (no
  per-entity workflow read exists) — bounded by the facade page size, flagged as a count rollup.
- The layer's coverage is bounded by the authoritative engines' read surface; a genuinely new automation
  signal is added to the owning engine first, then surfaces here.

## Enforcement
`tests/test_automation_orchestration.py` (three registries + single ownership; explainable dashboard
composition; authorization — unauthorized → None, unentitled panel restricted never valued; runtime + policy
gates; the firm summary + client/household rollups; analytics reuse — the 4 counters in the ONE registry;
diagnostics; routes registered + capability-gated; AI summarize-only; and the architecture invariants — no
second workflow engine / scheduler / event bus, no mutation, workflow reads composed from the Workflow
Engine, every dashboard deep-links, every execution summary names an authoritative owner).
`app/services/automation_orchestration/governance.py` enforces the invariants at runtime. Route count,
section registries, and migration head are guarded by `tests/test_platform_architecture.py` +
`tests/test_client360_workspace.py` + `tests/test_household360_workspace.py` + the manifest.

## Exceptions
Firm-global reads that do not self-gate (Workflow Engine metrics, Automation engine metrics, Event outbox
diagnostics) are exposed only within dashboards whose required capability (`automation.view`) the principal
holds; each panel additionally self-restricts to `automation.view`, so a value is never shown to a principal
lacking that capability.

## Revisit conditions
Revisit when a new automation signal is required (add it to the owning engine), when durable
automation-orchestration state is needed (extend the Workflow / Automation engines, never a second store), or
if a materialized automation read-model is ever justified (it would be a governed projection, never a second
orchestration engine).

## References
- `app/services/automation_orchestration/*` (`registry.py`, `model.py`, `service.py`, `panels.py`,
  `gate.py`, `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`, `__init__.py`)
- `app/routes/automation_orchestration.py`; Client 360 section in
  `app/services/client360/{registry,sections}.py`; Household 360 section in
  `app/services/client360/household.py`; Automation Status panel in `app/services/workspace/service.py`;
  Executive Automation dashboard in `app/services/executive_intelligence/registry.py`; AI grounding in
  `app/services/ai_assist/context.py`; analytics counters in `app/services/analytics/{sources,metrics}.py`
- Composes `app/services/workflow_automation.py`, `app/services/workflow_orchestration/*`,
  `app/services/automation/*`, `app/services/events/*`, `app/services/scheduling/*`,
  `app/services/communications/*`
- `docs/AUTOMATION_ORCHESTRATION.md`, `docs/AUTOMATION_REGISTRY.md`, `docs/TRIGGER_REGISTRY.md`,
  `docs/AUTOMATION_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_automation_orchestration.py`; relates to ADR-022, ADR-024, ADR-027, ADR-046 through ADR-055
