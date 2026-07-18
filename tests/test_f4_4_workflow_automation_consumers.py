"""F4.4 / Epic 4 — Workflow automation consumers acceptance tests (ADR-016).

Consumers subscribe to workflow lifecycle events (F4.3) and run configured
automation actions, exactly once, idempotently, retry-safe, and without ever
changing workflow state. Automation consumes events; events never consume
automation (no feedback loop).
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import automation_actions, engine, households, people, roles, user_roles, users
from app.platform.events import Envelope
from app.platform.outbox import (
    _subscribers,
    clear_subscribers,
    dispatch_pending,
    outbox_events,
    outbox_processed_events,
)
from app.services.workflow_automation import launch_workflow, workflow_detail
from app.services.workflow_automation_consumers import (
    WORKFLOW_EVENT_TYPES,
    clear_automation_registry,
    configured_actions,
    on_workflow_event,
    register_automation,
    register_workflow_consumers,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_TEMPLATE = "client_onboarding"


@pytest.fixture
def consumers():
    clear_subscribers()
    clear_automation_registry()
    yield
    clear_subscribers()
    clear_automation_registry()


def _actor():
    suffix = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"F44 {suffix}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(household_id=hid, full_name=f"F44 {suffix}", active=True).returning(people.c.id)).scalar_one()
        uid = c.execute(users.insert().values(
            email=f"f44-{suffix}@e.com", normalized_email=f"f44-{suffix}@e.com",
            display_name="f44", auth_subject=f"f44-{suffix}", status="active",
        ).returning(users.c.id)).scalar_one()
        role_id = c.scalar(select(roles.c.id).where(roles.c.code == "administrator"))
        if role_id:
            c.execute(user_roles.insert().values(user_id=uid, role_id=role_id))
    return uid, pid, hid


def _launch(uid, pid, hid):
    return launch_workflow(DB_TEMPLATE, actor_user_id=uid, person_id=pid, household_id=hid,
                           idempotency_key=f"f44-{uuid.uuid4()}")


def _action_count(instance_id, action_type="publish_timeline"):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(automation_actions).where(
            automation_actions.c.workflow_instance_id == instance_id,
            automation_actions.c.action_type == action_type)).scalar_one()


def _envelope(instance_id, event_type="workflow.launched"):
    return Envelope(event_type=event_type, event_id=f"f44-{uuid.uuid4()}",
                    payload={"workflow_instance_id": instance_id, "action": "launch"},
                    subject_ref=f"workflow_instance:{instance_id}")


# --- registration / subscription ---------------------------------------------

def test_consumers_subscribe_to_all_lifecycle_events(consumers):
    assert WORKFLOW_EVENT_TYPES == (
        "workflow.launched", "workflow.paused", "workflow.resumed",
        "workflow.cancelled", "workflow.completed", "workflow.reopened",
    )
    register_workflow_consumers()
    for et in WORKFLOW_EVENT_TYPES:
        assert on_workflow_event in _subscribers.get(et, [])


# --- exactly once / duplicate-safe (handler level) ---------------------------

def test_configured_action_runs_exactly_once_and_duplicate_is_noop(consumers):
    uid, pid, hid = _actor()
    instance_id = _launch(uid, pid, hid)
    register_automation("workflow.launched", "publish_timeline", payload={"title": "auto"})
    assert configured_actions("workflow.launched") == [{"action_type": "publish_timeline", "payload": {"title": "auto"}}]
    env = _envelope(instance_id)
    on_workflow_event(env)
    on_workflow_event(env)  # duplicate delivery — must not rerun (same idempotency_key)
    assert _action_count(instance_id) == 1


def test_no_configured_action_is_a_noop(consumers):
    uid, pid, hid = _actor()
    instance_id = _launch(uid, pid, hid)
    on_workflow_event(_envelope(instance_id, "workflow.completed"))  # nothing configured
    assert _action_count(instance_id) == 0


# --- exactly once through the outbox dispatcher ------------------------------

def test_exactly_once_through_dispatch_and_reprocess_is_prevented(consumers):
    uid, pid, hid = _actor()
    register_workflow_consumers()
    register_automation("workflow.launched", "publish_timeline", payload={"title": "auto"})
    instance_id = _launch(uid, pid, hid)  # emits workflow.launched to the outbox
    with engine.connect() as c:
        rows = c.execute(select(outbox_events).where(outbox_events.c.name == "workflow.launched")).mappings().all()
    event_id = next(r["event_id"] for r in rows if r["payload"].get("subject_ref") == f"workflow_instance:{instance_id}")

    dispatch_pending(batch_size=5000)
    assert _action_count(instance_id) == 1
    consumer = "app.services.workflow_automation_consumers.on_workflow_event"
    with engine.connect() as c:
        processed = c.execute(select(func.count()).select_from(outbox_processed_events).where(
            outbox_processed_events.c.event_id == event_id,
            outbox_processed_events.c.consumer == consumer)).scalar_one()
    assert processed == 1
    dispatch_pending(batch_size=5000)  # re-dispatch — already processed, no rerun
    assert _action_count(instance_id) == 1


# --- never changes workflow state --------------------------------------------

def test_automation_never_changes_workflow_state(consumers):
    uid, pid, hid = _actor()
    instance_id = _launch(uid, pid, hid)
    register_automation("workflow.launched", "publish_timeline", payload={"title": "auto"})
    status_before = workflow_detail(instance_id)["workflow"]["status"]
    steps_before = [s["status"] for s in workflow_detail(instance_id)["steps"]]
    on_workflow_event(_envelope(instance_id))
    assert workflow_detail(instance_id)["workflow"]["status"] == status_before
    assert [s["status"] for s in workflow_detail(instance_id)["steps"]] == steps_before


# --- events never consume automation (no feedback loop) ----------------------

def test_automation_does_not_publish_new_workflow_events(consumers):
    uid, pid, hid = _actor()
    instance_id = _launch(uid, pid, hid)
    register_automation("workflow.launched", "publish_timeline", payload={"title": "auto"})

    def _wf_event_count():
        with engine.connect() as c:
            rows = c.execute(select(outbox_events).where(outbox_events.c.name.like("workflow.%"))).mappings().all()
        return sum(1 for r in rows if r["payload"].get("subject_ref") == f"workflow_instance:{instance_id}")

    before = _wf_event_count()
    on_workflow_event(_envelope(instance_id))
    assert _wf_event_count() == before  # automation emits no new workflow lifecycle event


# --- retry-safe: a failing action propagates (for outbox retry/dead-letter) ---

def test_failing_action_propagates_for_retry(consumers):
    uid, pid, hid = _actor()
    instance_id = _launch(uid, pid, hid)
    register_automation("workflow.launched", "unsupported_action")
    with pytest.raises(ValueError):
        on_workflow_event(_envelope(instance_id))
    assert _action_count(instance_id, "unsupported_action") == 0  # rolled back; no partial


# --- no state transitions in the consumer (source contract) ------------------

def test_consumer_introduces_no_state_transitions_or_api(consumers):
    source = (REPO_ROOT / "app" / "services" / "workflow_automation_consumers.py").read_text()
    for forbidden in ("launch_workflow(", "transition_workflow(", "complete_step(",
                      "workflow_instances.update", "APIRouter"):
        assert forbidden not in source
    assert (REPO_ROOT / "docs" / "WORKFLOW_AUTOMATION.md").is_file()
