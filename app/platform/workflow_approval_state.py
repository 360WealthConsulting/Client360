"""Workflow approval state machine — formal specification (F4.5 / Epic 4, ADR-016).

A single, **pure** (DB-free), documented source of truth for the workflow **approval**
business process: its deterministic states, the decision set, separation-of-duty (SoD)
rules, and the reassignment guard. The approval functions in
``app/services/workflow_automation.py`` consume these validators, so the rules are
defined once and cannot drift.

Approvals are a business process **layered on** workflow execution (ADR-016):
- Deterministic: ``pending`` → ``approved`` | ``rejected``; a pending approval may be
  **reassigned** (stays pending, approver changes).
- SoD-enforced: an assigned approver cannot be the requester (also enforced by the DB
  ``ck_work_approval_segregation`` constraint).
- Independent of execution: approvals never transition or bypass workflow rules; they
  only gate step completion, which the engine checks separately.

All validator messages are preserved verbatim from the engine's prior inline checks so
existing behavior and callers are unchanged.
"""
from __future__ import annotations

#: Canonical approval states.
APPROVAL_STATES: frozenset[str] = frozenset({"pending", "approved", "rejected"})

#: Valid decision values for a pending approval.
APPROVAL_DECISIONS: frozenset[str] = frozenset({"approved", "rejected"})

#: Terminal (decided) approval states.
TERMINAL_APPROVAL_STATES: frozenset[str] = frozenset({"approved", "rejected"})


def is_pending(status: str) -> bool:
    return status == "pending"


def is_terminal(status: str) -> bool:
    return status in TERMINAL_APPROVAL_STATES


def can_reassign(status: str) -> bool:
    """A pending approval may be reassigned; a decided one may not."""
    return status == "pending"


# --- validators (raise ValueError; messages preserved) -----------------------

def validate_decision(decision: str) -> str:
    if decision not in APPROVAL_DECISIONS:
        raise ValueError("Decision must be approved or rejected")
    return decision


def validate_decidable(approval) -> None:
    """A decision requires a pending approval (found + pending)."""
    if not approval or approval["status"] != "pending":
        raise ValueError("Pending approval not found")


def validate_reassignable(approval) -> None:
    """Reassignment requires a pending approval."""
    if not approval or not can_reassign(approval["status"]):
        raise ValueError("Only a pending approval can be reassigned")


def check_independent_requester(requested_by_user_id, approver_user_id) -> None:
    """Request/reassign-time SoD: an assigned user-approver cannot be the requester."""
    if approver_user_id is not None and approver_user_id == requested_by_user_id:
        raise ValueError("Independent approval cannot be self-approved")


def check_decider_not_requester(requested_by_user_id, approver_user_id) -> None:
    """Decision-time SoD: the requester cannot approve their own work."""
    if requested_by_user_id == approver_user_id:
        raise ValueError("Requester cannot approve their own work")


def check_assigned_approver(assigned_user_id, actor_user_id) -> None:
    """Decision-time routing: only the assigned approver (if any) may decide."""
    if assigned_user_id and assigned_user_id != actor_user_id:
        raise ValueError("Approval is assigned to another user")
