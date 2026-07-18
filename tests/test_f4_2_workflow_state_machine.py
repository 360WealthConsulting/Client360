"""F4.2 / Epic 4 — Workflow state machine formalization acceptance tests (ADR-016).

Two layers:
1. Pure specification tests (no DB) — deterministic transitions, valid/invalid
   transitions, preserved rejection message, dependency rule, completion rule,
   and lifecycle invariants.
2. Engine-consumes-the-spec tests (DB) — the live engine drives transitions
   exactly as the formal spec predicts, rejects invalid transitions with the
   preserved message, and preserves idempotent/dependency behavior.

This feature formalizes existing behavior; it must not change it.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import engine, roles, user_roles, users, workflow_instances
from app.platform.workflow_state_machine import (
    ACTIVE_STEP_STATES,
    STEP_STATES,
    WORKFLOW_ACTIONS,
    WORKFLOW_STATES,
    WORKFLOW_TRANSITIONS,
    assert_lifecycle_invariants,
    dependencies_satisfied,
    instance_is_complete,
    is_valid_transition,
    next_state,
    valid_actions,
    validate_transition,
)
from app.services.workflow_automation import launch_workflow, transition_workflow, workflow_detail

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_TEMPLATE = "client_onboarding"


def _actor() -> int:
    suffix = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"f42-{suffix}@example.com", normalized_email=f"f42-{suffix}@example.com",
            display_name="f42", auth_subject=f"f42-{suffix}", status="active",
        ).returning(users.c.id)).scalar_one()
        role_id = c.scalar(select(roles.c.id).where(roles.c.code == "administrator"))
        if role_id:
            c.execute(user_roles.insert().values(user_id=uid, role_id=role_id))
    return uid


# --- pure specification ------------------------------------------------------

def test_transition_table_is_deterministic_and_closed():
    # Determinism: each (state, action) maps to exactly one target, itself a valid state.
    for state, actions in WORKFLOW_TRANSITIONS.items():
        assert state in WORKFLOW_STATES
        for action, target in actions.items():
            assert isinstance(target, str) and target in WORKFLOW_STATES
            assert next_state(state, action) == target
    assert ACTIVE_STEP_STATES == ("active", "pending", "paused")
    assert set(ACTIVE_STEP_STATES) <= STEP_STATES


def test_valid_and_invalid_transitions():
    assert next_state("active", "pause") == "paused"
    assert next_state("paused", "resume") == "active"
    assert next_state("cancelled", "reopen") == "active"
    assert next_state("completed", "reopen") == "active"
    assert next_state("active", "resume") is None          # invalid
    assert next_state("paused", "complete") is None         # invalid
    assert next_state("nonsense", "pause") is None          # unknown state
    assert is_valid_transition("active", "cancel") is True
    assert is_valid_transition("cancelled", "pause") is False
    assert valid_actions("active") == frozenset({"pause", "cancel", "complete"})
    assert valid_actions("nonsense") == frozenset()
    assert WORKFLOW_ACTIONS == frozenset({"pause", "resume", "cancel", "complete", "reopen"})


def test_validate_transition_message_is_preserved():
    assert validate_transition("active", "pause") == "paused"
    with pytest.raises(ValueError, match=r"Cannot resume a active workflow"):
        validate_transition("active", "resume")
    with pytest.raises(ValueError, match=r"Cannot pause a cancelled workflow"):
        validate_transition("cancelled", "pause")


def test_dependency_and_completion_rules():
    assert dependencies_satisfied([], [1, 2]) is True             # no deps
    assert dependencies_satisfied([1, 2], [1, 2, 3]) is True      # all satisfied
    assert dependencies_satisfied([1, 2], [1]) is False           # missing dep
    assert instance_is_complete([]) is True
    assert instance_is_complete(["completed", "skipped", "cancelled"]) is True
    assert instance_is_complete(["completed", "active"]) is False
    assert instance_is_complete(["pending"]) is False


def test_lifecycle_invariants():
    assert_lifecycle_invariants("active", ["active", "pending"])          # ok
    assert_lifecycle_invariants("cancelled", ["cancelled", "skipped"])    # ok
    with pytest.raises(AssertionError):
        assert_lifecycle_invariants("nonsense", [])
    with pytest.raises(AssertionError):
        assert_lifecycle_invariants("active", ["bogus_step_state"])
    with pytest.raises(AssertionError):
        assert_lifecycle_invariants("cancelled", ["active"])  # cancelled must have no unfinished step


# --- engine consumes the spec (behavior preserved) ---------------------------

def _launch(actor) -> int:
    return launch_workflow(DB_TEMPLATE, actor_user_id=actor, idempotency_key=f"f42-{uuid.uuid4()}")


def test_engine_transitions_follow_the_formal_spec():
    actor = _actor()
    instance_id = _launch(actor)
    for action in ("pause", "resume", "cancel", "reopen"):
        status_before = workflow_detail(instance_id)["workflow"]["status"]
        expected = next_state(status_before, action)
        assert transition_workflow(instance_id, action, actor_user_id=actor) == expected
        assert workflow_detail(instance_id)["workflow"]["status"] == expected


def test_engine_rejects_invalid_transition_with_preserved_message():
    actor = _actor()
    instance_id = _launch(actor)  # starts "active"
    with pytest.raises(ValueError, match=r"Cannot resume a active workflow"):
        transition_workflow(instance_id, "resume", actor_user_id=actor)
    # Re-applying a transition is rejected by the state machine (deterministic, safe).
    transition_workflow(instance_id, "pause", actor_user_id=actor)
    with pytest.raises(ValueError, match=r"Cannot pause a paused workflow"):
        transition_workflow(instance_id, "pause", actor_user_id=actor)


def test_engine_cancel_clears_active_steps_and_satisfies_invariants():
    actor = _actor()
    instance_id = _launch(actor)
    transition_workflow(instance_id, "cancel", actor_user_id=actor)
    data = workflow_detail(instance_id)
    statuses = [s["status"] for s in data["steps"]]
    assert all(s not in ACTIVE_STEP_STATES for s in statuses)
    assert_lifecycle_invariants(data["workflow"]["status"], statuses)


def test_live_instances_satisfy_lifecycle_invariants():
    actor = _actor()
    instance_id = _launch(actor)
    with engine.connect() as c:
        status = c.execute(select(workflow_instances.c.status)
                           .where(workflow_instances.c.id == instance_id)).scalar_one()
    statuses = [s["status"] for s in workflow_detail(instance_id)["steps"]]
    assert_lifecycle_invariants(status, statuses)


def test_state_machine_is_pure_and_documented():
    source = (REPO_ROOT / "app" / "platform" / "workflow_state_machine.py").read_text()
    assert "APIRouter" not in source
    assert "from app.db" not in source and "sqlalchemy" not in source  # DB-free / pure
    assert (REPO_ROOT / "docs" / "WORKFLOW_STATE_MACHINE.md").is_file()
