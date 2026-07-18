# Client360 — Workflow State Machine (F4.2 / Epic 4)

A single, **pure** (DB-free), documented specification of the workflow execution
model's deterministic lifecycle. F4.2 **formalizes existing behavior** — it does
not change it. The execution engine consumes these definitions, so the state
machine is defined **once** and cannot drift between spec and implementation.

`app/platform/workflow_state_machine.py`

## Reconciliation (ADR-013 / ADR-016)
- **Engine preserved.** `app/services/workflow_automation.py` keeps all business
  behavior. F4.2 only *extracts* the state machine it already implemented into an
  authoritative module and has the engine import it — a behavior-identical change
  proven by the unchanged engine test suite staying green.
- **No new capability, schema, route, event, or automation.** Pure functions and
  constants only. No migration.

## Instance lifecycle (deterministic)
States: `active`, `paused`, `cancelled`, `completed`. Transition table
(`state → {action → next_state}`) — at most one target per `(state, action)`:

| From | `pause` | `resume` | `cancel` | `complete` | `reopen` |
|---|---|---|---|---|---|
| **active** | paused | — | cancelled | completed | — |
| **paused** | — | active | cancelled | — | — |
| **cancelled** | — | — | — | — | active |
| **completed** | — | — | — | — | active |

An invalid `(state, action)` is rejected with the preserved message
`Cannot {action} a {state} workflow`.

## Step model
Step statuses: `pending`, `active`, `paused`, `skipped`, `completed`, `cancelled`.
`ACTIVE_STEP_STATES = ("active", "pending", "paused")` are the statuses that keep an
instance "not yet complete".

## Rules (pure functions)
```python
from app.platform.workflow_state_machine import (
    next_state, is_valid_transition, valid_actions, validate_transition,
    dependencies_satisfied, instance_is_complete, assert_lifecycle_invariants,
)
next_state("active", "pause")            # "paused"; None if not permitted
validate_transition("active", "resume")  # raises ValueError (preserved message)
dependencies_satisfied(deps, satisfied)  # a step runs when deps ⊆ completed/skipped
instance_is_complete(step_statuses)      # True when no step is active/pending/paused
assert_lifecycle_invariants(status, step_statuses)  # snapshot consistency
```

- **Dependency rule:** a pending step activates once all its dependency template
  steps are completed or skipped (`deps ⊆ satisfied`).
- **Completion rule:** an instance auto-completes when no step remains
  active/pending/paused.

## Lifecycle invariants (only real guarantees)
`assert_lifecycle_invariants` encodes exactly what the engine guarantees:
- the instance status is a known state; every step status is a known step state;
- a **cancelled** instance retains no active/pending/paused steps (the engine
  cancels them).

It deliberately does **not** assert that a `completed` instance has no unfinished
steps, because a manual `complete` transition does not cascade to steps — that is
existing behavior, preserved.

## Determinism & reproducibility
Transition resolution and the dependency/completion rules are pure functions of
their inputs — same inputs always yield the same result, independent of the
database or wall-clock. This makes the execution model reproducible and testable in
isolation (see `tests/test_f4_2_workflow_state_machine.py`).

## Compatibility (ADR-016 Compatibility Contract)
Public routes, service signatures, execution semantics, DB/automation guarantees,
idempotency, and the route inventory (306) are **unchanged**. No new capability, no
event emission, no schema change.

## References
ADR-013, ADR-016; `docs/WORKFLOW_EXECUTION.md` (F4.1), `docs/WORKFLOW_TEMPLATES.md`
(F1.5); `app/services/workflow_automation.py` (the canonical engine).
