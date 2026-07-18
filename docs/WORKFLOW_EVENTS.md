# Client360 — Workflow Events (F4.3 / Epic 4)

Deterministic publication of workflow **lifecycle transition** events onto the
existing platform event infrastructure — the F1.4 event envelope over the F1.3
transactional outbox. Per ADR-016, **events are notifications of state changes, not
drivers of them**: this feature only *emits*. It registers no subscribers, changes
no workflow state, and performs no automation, advancement, or SLA processing.

`app/platform/workflow_events.py`

## Reconciliation (ADR-013 / ADR-016)
- **Engine remains the source of truth.** `app/services/workflow_automation.py`
  computes and commits state; F4.3 adds an *additive* emission call inside the
  engine's existing transaction. Existing behavior (the F4.2 state machine, the
  domain `workflow_events` ledger, timeline, audit) is unchanged.
- **Uses existing infrastructure only.** No new tables — envelopes serialize into
  `outbox_events.payload` (F1.4). No migration. Outbox stays OFF by default.

## What is published
One F1.4 envelope per **instance lifecycle transition**:

| Trigger | Event type |
|---|---|
| `launch_workflow` | `workflow.launched` |
| `transition_workflow("pause")` | `workflow.paused` |
| `transition_workflow("resume")` | `workflow.resumed` |
| `transition_workflow("cancel")` | `workflow.cancelled` |
| `transition_workflow("complete")` | `workflow.completed` |
| `transition_workflow("reopen")` | `workflow.reopened` |

Each envelope: `subject_ref="workflow_instance:<id>"`, `producer="workflow.execution"`,
`correlation_id="workflow_instance:<id>"` (ties an instance's flow), reference-only
`payload` (`workflow_instance_id`, `action`, and `from`/`to` states on transitions),
and `metadata` (`domain_event_id`, `actor_user_id`, optional `reason`).

## Atomicity, determinism & idempotency
- **Atomic:** emitted with the engine's connection, so an event exists **iff** the
  transition commits (the transactional-outbox guarantee). An invalid/rejected
  transition emits nothing.
- **Exactly one per transition:** each transition creates one domain `workflow_events`
  row and emits one envelope.
- **Deterministic id:** the envelope `event_id` is `uuid5(namespace, workflow_event:<domain_event_id>)`,
  so re-emitting the same transition yields the same id.
- **Duplicate prevented:** emission is a no-op if the id already exists, and the
  outbox `uq_outbox_events_event_id` unique constraint is the backstop.

## Reference-only (privacy)
Payloads and metadata carry references only — ids, states, actor id — never PII,
secrets, or return data (Constitution §9).

## Extension points (future features)
- Consumers subscribe via the existing outbox seam
  (`app.platform.subscribe(event_type, handler)`); the dispatcher delivers
  at-least-once with idempotency. **F4.3 registers none** — reactions/advancement,
  automation (F4.4), and SLA/approval/step event types are deferred.
- `TRANSITION_EVENT_TYPES` / `transition_event_type` define the canonical taxonomy
  for those future consumers.

## Compatibility (ADR-016 Compatibility Contract)
Public routes, service signatures, execution semantics, the state machine, DB and
automation guarantees, and the route inventory (306) are **unchanged**. No new
capability, no schema change, no state change from events.

## References
ADR-013, ADR-015, ADR-016; `docs/OUTBOX.md` (F1.3), `docs/EVENTS.md` (F1.4),
`docs/WORKFLOW_STATE_MACHINE.md` (F4.2), `docs/WORKFLOW_EXECUTION.md` (F4.1);
`app/services/workflow_automation.py` (the canonical engine).
