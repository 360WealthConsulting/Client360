# Client360 — Workflow SLA & Escalation Engine (F4.6 / Epic 4)

Deterministic SLA deadline tracking and escalation processing for workflow steps.
SLA processing **observes** workflow state (a step's deadline) but **never drives
execution** (ADR-016): it decides whether/how to escalate and records escalations,
events, and audit — it never transitions or completes a workflow. Workflow execution
remains the source of truth.

`app/platform/workflow_sla.py` (pure policy) · `evaluate_sla` in
`app/services/workflow_automation.py`

## Reconciliation (ADR-013 / ADR-016)
- **Engine preserved.** The existing `evaluate_sla` behavior (scan overdue active
  steps → create one level-1 `sla_breach` escalation, idempotently) is preserved; F4.6
  **formalizes** the deadline/policy decision into a pure module and **adds** escalation
  event publication and audit records. The `workflow_escalations` table and its
  `(workflow_step_id, escalation_type, level)` unique constraint are unchanged.
- **Additive, no migration.** No schema change, no new capability, no new route. The
  scheduler job (`run_workflow_sla_automation`, every 5 min) is unchanged.

## Deterministic policy (pure)
```python
from app.platform.workflow_sla import is_overdue, evaluate_escalation, set_escalation_policy
is_overdue(sla_due_at, now)          # True iff sla_due_at is set and in the past
evaluate_escalation(sla_due_at, now) # {"escalation_type","level"} when breached, else None
```
The default policy mirrors existing behavior — one level-1 `sla_breach` when a step's
deadline has passed. Decisions are pure functions of `(sla_due_at, now)`, so evaluation
is reproducible.

## Idempotent, retry-safe processing
`evaluate_sla` is safe to run repeatedly (the scheduler runs it every 5 minutes):
- an escalation is created **once** per `(step, escalation_type, level)` — guarded by an
  existence check and the DB unique constraint;
- the domain `sla_escalated` event uses a deterministic idempotency key
  (`step:<id>:sla:<level>`);
- the `workflow.sla.escalated` envelope uses a deterministic `event_id` (skip-if-exists
  + outbox unique constraint);
- re-runs for an already-escalated step are **no-ops** (no new escalation, event,
  envelope, or audit). Exactly once.

## Escalation events + audit (published, not consumed)
Each new escalation publishes one F1.4 envelope over the F1.3 outbox —
`workflow.sla.escalated` (`subject_ref="workflow_instance:<id>"`,
`producer="workflow.sla"`, reference-only `{workflow_instance_id, workflow_step_id,
escalation_id, escalation_type, level}`) — and writes a tamper-evident audit record
(F3.1 / ADR-015). SLA events are **not** lifecycle transitions, so the F4.4 automation
consumer (which subscribes only to the six lifecycle types) does not react to them.

## SLA never changes workflow state
`evaluate_sla` only reads step deadlines and inserts into `workflow_escalations` /
`workflow_events` / the outbox / the audit log. It never mutates `workflow_instances`
or `workflow_steps` status — escalation is independent of workflow execution.

## Extension points
- `set_escalation_policy(policy)` / `reset_escalation_policy()` — plug in a multi-level
  or domain-specific escalation policy without changing the engine (default is
  level-1 `sla_breach`).

## Out of scope (later features)
Dashboard/reporting, UI/API surface (F4.8), new capabilities, and workflow state
mutation.

## Compatibility (ADR-016 Compatibility Contract)
Public routes, service signatures, execution semantics, the state machine, event
publication, automation consumers, the approval engine, DB guarantees, and the route
inventory (306) are **unchanged**. No new capability, no schema change, no migration.

## References
ADR-013, ADR-015, ADR-016; `docs/WORKFLOW_EVENTS.md` (F4.3),
`docs/WORKFLOW_AUTOMATION.md` (F4.4), `docs/WORKFLOW_APPROVALS.md` (F4.5);
`app/services/workflow_automation.py`.
