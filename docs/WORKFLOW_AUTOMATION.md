# Client360 — Workflow Automation Consumers (F4.4 / Epic 4)

Event-driven automation for workflows. Consumers **subscribe** to the workflow
lifecycle events published by F4.3 (over the F1.3 outbox) and run **configured
automation actions**. This is the first *consumer* layer of Epic 4.

`app/services/workflow_automation_consumers.py`

## Direction & source of truth (ADR-016)
Strictly one-way: **automation consumes workflow events; workflow events never
consume automation.** Workflow execution remains the source of truth. Automation
actions run through the existing idempotent ledger (`execute_automation_action`) and
**never change workflow state** — they never launch, transition, complete, or advance
a workflow, and never read workflow status for control flow.

## How it works
```python
from app.services.workflow_automation_consumers import (
    register_automation, register_workflow_consumers,
)
# Configure an action to run when a lifecycle event is observed (extension point):
register_automation("workflow.completed", "publish_timeline", payload={"title": "Done"})
# Subscribe the consumer to every workflow lifecycle event type:
register_workflow_consumers()
```
On each delivered event, `on_workflow_event` runs the configured actions for that
event type via `execute_automation_action(instance_id, action_type,
idempotency_key="auto:<event_id>:<i>:<action>")`.

## Guarantees
- **Event-driven:** actions run only in response to a published lifecycle event.
- **Exactly once / duplicate-safe:** two independent idempotency layers — the outbox
  records `(event_id, consumer)` in `outbox_processed_events` (a handler is never
  re-run for an event it already processed), and each action's deterministic
  `idempotency_key` means a redelivery cannot re-execute it.
- **Retry-safe:** a failing action raises; the outbox backs off and retries, then
  dead-letters after `MAX_ATTEMPTS` (operator-visible). Idempotency means retries
  never double-execute. A failed action rolls back — no partial state.
- **Independent of state transitions:** no workflow status is written and none is read
  for control flow.
- **No feedback loop:** automation writes to the action/timeline/domain-event ledgers
  but publishes **no** new workflow lifecycle envelope, so it cannot re-trigger itself.

## Activation (dark launch)
Consumers are registered only when the outbox dispatcher is enabled
(`OUTBOX_DISPATCHER_ENABLED`, in `app/jobs/scheduler.py`). By default the dispatcher
is OFF, so **no subscribers are registered** and runtime behavior — and F4.3's "no
subscribers" guarantee — is unchanged until explicitly enabled.

## Configuration seam (extension point)
`register_automation(event_type, action_type, payload=...)` builds the
`event_type → [actions]` registry. F4.4 ships the **mechanism**, not domain
automations — the registry is empty by default. Supported action types are provided
by the engine's `execute_automation_action` (currently `publish_timeline`); new
action types are added there via a domain adapter.

## Out of scope (later features)
Approval routing (F4.5), SLA processing (F4.6), UI, workflow state transitions, and
new workflow capabilities.

## Compatibility (ADR-016 Compatibility Contract)
Public routes, service signatures, the engine, the state machine, event publication,
DB guarantees, and the route inventory (306) are **unchanged**. No new capability, no
schema change, no migration.

## References
ADR-013, ADR-015, ADR-016; `docs/WORKFLOW_EVENTS.md` (F4.3), `docs/OUTBOX.md` (F1.3),
`docs/EVENTS.md` (F1.4), `docs/WORKFLOW_STATE_MACHINE.md` (F4.2);
`app/services/workflow_automation.py` (the engine + action ledger).
