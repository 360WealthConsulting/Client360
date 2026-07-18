"""F4.3 / Epic 4 — Workflow event publication acceptance tests (ADR-016).

Every lifecycle transition publishes exactly one F1.4 envelope over the F1.3
outbox; publication is deterministic and idempotent (duplicates prevented);
events never change workflow state; and existing behavior is preserved.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import func, select

from app.db import engine, roles, user_roles, users
from app.platform.events import Envelope
from app.platform.outbox import outbox_events
from app.platform.workflow_events import (
    TRANSITION_EVENT_TYPES,
    emit_transition_event,
    transition_event_type,
    workflow_event_id,
)
from app.services.workflow_automation import launch_workflow, transition_workflow, workflow_detail

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_TEMPLATE = "client_onboarding"


def _actor() -> int:
    suffix = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"f43-{suffix}@example.com", normalized_email=f"f43-{suffix}@example.com",
            display_name="f43", auth_subject=f"f43-{suffix}", status="active",
        ).returning(users.c.id)).scalar_one()
        role_id = c.scalar(select(roles.c.id).where(roles.c.code == "administrator"))
        if role_id:
            c.execute(user_roles.insert().values(user_id=uid, role_id=role_id))
    return uid


def _events_for(instance_id: int) -> list[Envelope]:
    subject = f"workflow_instance:{instance_id}"
    with engine.connect() as c:
        rows = c.execute(select(outbox_events).where(outbox_events.c.name.like("workflow.%"))
                         .order_by(outbox_events.c.id)).mappings().all()
    return [Envelope.from_dict(r["payload"]) for r in rows if r["payload"].get("subject_ref") == subject]


def _launch(actor) -> int:
    return launch_workflow(DB_TEMPLATE, actor_user_id=actor, idempotency_key=f"f43-{uuid.uuid4()}")


# --- taxonomy / determinism (pure) -------------------------------------------

def test_event_type_taxonomy_and_deterministic_id():
    assert transition_event_type("pause") == "workflow.paused"
    assert transition_event_type("launch") == "workflow.launched"
    assert set(TRANSITION_EVENT_TYPES) == {"launch", "pause", "resume", "cancel", "complete", "reopen"}
    # Deterministic + stable event id per domain-event id.
    assert workflow_event_id(123) == workflow_event_id(123)
    assert workflow_event_id(123) != workflow_event_id(124)
    assert len(workflow_event_id(1)) == 36


# --- exactly one event per transition ----------------------------------------

def test_launch_publishes_exactly_one_launched_event():
    actor = _actor()
    instance_id = _launch(actor)
    events = _events_for(instance_id)
    assert len(events) == 1
    e = events[0]
    assert e.event_type == "workflow.launched"
    assert e.subject_ref == f"workflow_instance:{instance_id}"
    assert e.payload["workflow_instance_id"] == instance_id and e.payload["action"] == "launch"
    assert e.producer == "workflow.execution"


def test_each_transition_publishes_exactly_one_event():
    actor = _actor()
    instance_id = _launch(actor)  # 1 event (launched)
    for action in ("pause", "resume", "cancel", "reopen"):
        transition_workflow(instance_id, action, actor_user_id=actor)
    events = _events_for(instance_id)
    assert [e.event_type for e in events] == [
        "workflow.launched", "workflow.paused", "workflow.resumed", "workflow.cancelled", "workflow.reopened",
    ]
    # from/to states recorded on transition events (references only).
    paused = next(e for e in events if e.event_type == "workflow.paused")
    assert paused.payload["from"] == "active" and paused.payload["to"] == "paused"


# --- idempotency / duplicate prevention --------------------------------------

def test_duplicate_publication_is_prevented():
    actor = _actor()
    instance_id = _launch(actor)
    with engine.connect() as c:
        before = c.execute(select(func.count()).select_from(outbox_events)
                           .where(outbox_events.c.name.like("workflow.%"))).scalar_one()
    # Re-emit for the SAME domain event id — must be a no-op (same deterministic id).
    # Derive a unique forged id from the (unique) instance so the deterministic
    # outbox event_id cannot collide with earlier non-reset runs.
    dei = instance_id * 100 + 1
    with engine.begin() as c:
        eid1 = emit_transition_event(c, instance_id=instance_id, action="pause", domain_event_id=dei)
        eid2 = emit_transition_event(c, instance_id=instance_id, action="pause", domain_event_id=dei)
    assert eid1 == eid2
    with engine.connect() as c:
        after = c.execute(select(func.count()).select_from(outbox_events)
                          .where(outbox_events.c.event_id == eid1)).scalar_one()
        total = c.execute(select(func.count()).select_from(outbox_events)
                          .where(outbox_events.c.name.like("workflow.%"))).scalar_one()
    assert after == 1                 # exactly one row for that deterministic id
    assert total == before + 1        # only one new event despite two emit calls


# --- events never change workflow state --------------------------------------

def test_events_never_change_workflow_state():
    actor = _actor()
    instance_id = _launch(actor)
    status_before = workflow_detail(instance_id)["workflow"]["status"]
    steps_before = [s["status"] for s in workflow_detail(instance_id)["steps"]]
    with engine.begin() as c:
        emit_transition_event(c, instance_id=instance_id, action="complete", domain_event_id=instance_id * 100 + 7)
    assert workflow_detail(instance_id)["workflow"]["status"] == status_before
    assert [s["status"] for s in workflow_detail(instance_id)["steps"]] == steps_before


# --- atomicity: event exists iff the transition committed ---------------------

def test_event_is_atomic_with_transition():
    actor = _actor()
    instance_id = _launch(actor)
    # A rejected transition (invalid) must not emit an event.
    before = len(_events_for(instance_id))
    try:
        transition_workflow(instance_id, "resume", actor_user_id=actor)  # invalid from active
    except ValueError:
        pass
    assert len(_events_for(instance_id)) == before


# --- backward compatibility / notification-only ------------------------------

def test_no_subscribers_registered_and_reference_only():
    # F4.3 emits only — no reactions/advancement: no workflow.* subscribers registered.
    from app.platform.outbox import _subscribers
    assert not any(name.startswith("workflow.") for name in _subscribers)
    source = (REPO_ROOT / "app" / "platform" / "workflow_events.py").read_text()
    assert "APIRouter" not in source
    assert (REPO_ROOT / "docs" / "WORKFLOW_EVENTS.md").is_file()
