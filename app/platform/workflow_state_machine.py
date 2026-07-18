"""Workflow lifecycle state machine — formal specification (F4.2 / Epic 4, ADR-016).

A single, **pure** (DB-free), documented source of truth for the workflow
execution model's deterministic transitions, step-status model, dependency-
satisfaction rule, and lifecycle invariants. The execution engine
(``app/services/workflow_automation.py``) consumes these definitions, so the state
machine is defined **once** and cannot drift between spec and implementation.

This feature **formalizes existing behavior — it does not change it.** Every value
and rule here mirrors the engine's prior inline behavior exactly: identical
transitions, identical rejection message, identical active-step-state set, and the
identical dependency rule. Determinism: for any ``(state, action)`` there is at
most one next state, so transition resolution is a pure function.
"""
from __future__ import annotations

from collections.abc import Iterable

# --- instance lifecycle ------------------------------------------------------

#: Canonical instance lifecycle states.
WORKFLOW_STATES: frozenset[str] = frozenset({"active", "paused", "cancelled", "completed"})

#: Authoritative transition table: ``state -> {action -> next_state}``. Deterministic
#: (at most one target per ``(state, action)``). Mirrors the engine's prior inline map.
WORKFLOW_TRANSITIONS: dict[str, dict[str, str]] = {
    "active": {"pause": "paused", "cancel": "cancelled", "complete": "completed"},
    "paused": {"resume": "active", "cancel": "cancelled"},
    "cancelled": {"reopen": "active"},
    "completed": {"reopen": "active"},
}

#: The set of recognized lifecycle actions (derived from the table).
WORKFLOW_ACTIONS: frozenset[str] = frozenset(
    action for targets in WORKFLOW_TRANSITIONS.values() for action in targets
)

# --- step model --------------------------------------------------------------

#: Canonical step statuses.
STEP_STATES: frozenset[str] = frozenset(
    {"pending", "active", "paused", "skipped", "completed", "cancelled"}
)

#: Step statuses that keep an instance "not yet complete" (the engine's remaining set).
ACTIVE_STEP_STATES: tuple[str, ...] = ("active", "pending", "paused")


# --- pure transition functions ----------------------------------------------

def next_state(status: str, action: str) -> str | None:
    """Return the deterministic next state for ``(status, action)``, or ``None`` if
    the transition is not permitted."""
    return WORKFLOW_TRANSITIONS.get(status, {}).get(action)


def is_valid_transition(status: str, action: str) -> bool:
    """Whether ``(status, action)`` is a permitted transition."""
    return next_state(status, action) is not None


def valid_actions(status: str) -> frozenset[str]:
    """The permitted actions from a given state (empty for unknown states)."""
    return frozenset(WORKFLOW_TRANSITIONS.get(status, {}))


def validate_transition(status: str, action: str) -> str:
    """Return the next state for ``(status, action)`` or raise ``ValueError``.

    The message is preserved exactly from the engine's prior inline guard so that
    existing behavior and callers are unchanged.
    """
    target = next_state(status, action)
    if target is None:
        raise ValueError(f"Cannot {action} a {status} workflow")
    return target


# --- deterministic execution rules ------------------------------------------

def dependencies_satisfied(dependency_ids: Iterable, satisfied_ids: Iterable) -> bool:
    """A step is runnable when **all** its dependency template-step ids are satisfied
    (completed or skipped). Mirrors the engine's ``dependencies <= completed``."""
    return set(dependency_ids) <= set(satisfied_ids)


def instance_is_complete(step_statuses: Iterable[str]) -> bool:
    """An instance auto-completes when **no** step remains active/pending/paused."""
    return not any(status in ACTIVE_STEP_STATES for status in step_statuses)


# --- invariants --------------------------------------------------------------

def assert_lifecycle_invariants(status: str, step_statuses: Iterable[str]) -> None:
    """Assert the formal lifecycle invariants for an instance snapshot.

    Deterministic, side-effect-free checks (used by tests and callers) verifying a
    workflow snapshot is internally consistent with the guarantees the engine
    actually provides. Raises ``AssertionError`` on violation.

    Encodes only real guarantees: valid state/step-status membership, and that a
    **cancelled** instance retains no active/pending/paused steps (the engine
    cancels them). It deliberately does *not* assert that a ``completed`` instance
    has no unfinished steps, because a manual ``complete`` transition does not
    cascade to steps (existing behavior, preserved).
    """
    statuses = list(step_statuses)
    assert status in WORKFLOW_STATES, f"unknown workflow state: {status!r}"
    for s in statuses:
        assert s in STEP_STATES, f"unknown step state: {s!r}"
    if status == "cancelled":
        assert all(s not in ACTIVE_STEP_STATES for s in statuses), \
            "cancelled instance retains an unfinished (active/pending/paused) step"
