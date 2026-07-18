# Client360 — Workflow Approval Engine (F4.5 / Epic 4)

Formalizes the workflow **approval** business process: deterministic approval state
transitions, separation-of-duty (SoD) enforcement, routing, independent approvals,
rejection handling, reassignment, approval event publication, and approval audit
records. Approvals are **layered on** workflow execution (ADR-016) — they never
transition or bypass workflow rules; workflow execution remains the source of truth.

`app/platform/workflow_approval_state.py` (pure spec) · approval functions in
`app/services/workflow_automation.py`

## Reconciliation (ADR-013 / ADR-016)
- **Engine preserved.** The existing `request_approval` / `decide_approval` behavior
  (signatures, effects, error messages) is preserved; F4.5 **adds** formal validators,
  approval event publication, audit records, and reassignment. The `work_approvals`
  table, its `ck_work_approval_segregation` DB constraint, and the `complete_step`
  approval gate are unchanged.
- **Additive, no migration.** No schema change, no new capability, no new route.

## Deterministic approval state
`pending → approved | rejected`; a **pending** approval may be **reassigned** (stays
pending, approver changes). Rules are pure functions in `workflow_approval_state.py`
(messages preserved verbatim):
```python
validate_decision(decision)                 # "approved"/"rejected" only
validate_decidable(approval)                # must be found + pending
validate_reassignable(approval)             # must be pending
check_independent_requester(req, approver)  # SoD: approver != requester
check_decider_not_requester(req, actor)     # SoD: requester cannot self-approve
check_assigned_approver(assigned, actor)    # routing: only the assigned approver decides
```

## Services
```python
from app.services.workflow_automation import request_approval, decide_approval, reassign_approval
approval_id = request_approval(step_id, requested_by_user_id=r, approver_user_id=a)
decide_approval(approval_id, approver_user_id=a, decision="approved", notes="ok")
reassign_approval(approval_id, reassigned_by_user_id=r, new_approver_user_id=a2, reason="OOO")
```

## Separation of duties (SoD)
Enforced at three points (in code and, for independent approvals, at the DB level):
- **Request/reassign:** the assigned approver cannot be the requester.
- **Decision:** the requester cannot approve their own work; only the assigned approver
  may decide.

## Reassignment preserves audit history
Reassignment updates the approver on the (pending) `work_approvals` row, but the full
assignment history is preserved in the **append-only** `workflow_events` ledger
(`approval_requested` → `approval_reassigned` (from/to) → `approval_decided`) and in the
tamper-evident **audit** records (F3.1/ADR-015) written for each step. No history is
overwritten or lost.

## Approval events (published, not consumed)
Each approval action publishes exactly one F1.4 envelope over the F1.3 outbox
(deterministic, duplicate-safe — same mechanism as F4.3):
`workflow.approval.requested`, `workflow.approval.decided`, `workflow.approval.reassigned`
(`subject_ref="workflow_instance:<id>"`, `producer="workflow.approvals"`, reference-only).
These are **not** lifecycle transitions, so the F4.4 automation consumer (which
subscribes only to the six lifecycle types) does not react to them.

## Approvals never bypass workflow rules
A decision **records** the approval; it does **not** complete the step or advance the
workflow. Step completion still goes through the engine's approval gate
(`complete_step` requires an approved `work_approvals` row). Approvals are independent
of workflow execution.

## Out of scope (later features)
SLA escalation (F4.6), UI/API surface (F4.8), reporting, and new capabilities.

## Compatibility (ADR-016 Compatibility Contract)
Public routes, service signatures, execution semantics, event publication, the state
machine, automation consumers, DB guarantees, and the route inventory (306) are
**unchanged**. No new capability, no schema change, no migration.

## References
ADR-013, ADR-015, ADR-016; `docs/WORKFLOW_EVENTS.md` (F4.3),
`docs/WORKFLOW_AUTOMATION.md` (F4.4), `docs/WORKFLOW_STATE_MACHINE.md` (F4.2);
`app/services/workflow_automation.py`.
