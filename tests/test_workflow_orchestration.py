"""Workflow orchestration tests (Phase D.17).

Covers launch/list/get + record scope, template listing (reuse), step completion + dependency
advancement, manual-approval gate, per-step retry (budget + exhaustion), step assignment,
pause/resume/cancel lifecycle, trigger configuration + validation + failure-isolated fire, action
registry, Analytics active_workflows consumption, audit history, and dependency direction. The
existing engine, /workflows routes, work.* capabilities, and tax launcher are not touched.
"""
import re
import uuid
from datetime import date

import pytest
from sqlalchemy import delete, insert, select, update
from starlette.requests import Request

from app.db import (
    engine,
    people,
    record_assignments,
    users,
    workflow_instances,
    workflow_steps,
)
from app.security.models import Principal
from app.services.workflow_orchestration import actions as wact
from app.services.workflow_orchestration import service as wsvc
from app.services.workflow_orchestration import triggers as wtrig

CAPS = frozenset({"workflow.view", "workflow.edit", "workflow.execute", "workflow.cancel",
                  "workflow.template_manage", "workflow.admin", "workflow.audit", "record.read_all"})


def _sfx():
    return uuid.uuid4().hex[:8]


def _setup():
    tag = _sfx()
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"wf-{tag}@e.test", normalized_email=f"wf-{tag}@e.test",
            display_name=f"U {tag}", status="active").returning(users.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"Client {tag}", primary_email=f"{tag}@e.test",
            normalized_email=f"{tag}@e.test", active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(record_assignments).values(
            user_id=uid, entity_type="person", entity_id=pid, assignment_type="owner",
            effective_date=date.today()))
    return {"uid": uid, "pid": pid, "tag": tag}


def _teardown(ids):
    from app.db import automation_triggers
    with engine.begin() as c:
        # workflow_events is append-only -> workflow instances cannot be deleted (CASCADE would
        # hit the trigger). Detach them from the person (mutable) and leave them as leftovers.
        c.execute(update(workflow_instances).where(workflow_instances.c.person_id == ids["pid"])
                  .values(person_id=None))
        c.execute(delete(automation_triggers).where(automation_triggers.c.name.like(f"%{ids['tag']}%")))
        c.execute(delete(record_assignments).where(record_assignments.c.user_id == ids["uid"]))
        c.execute(delete(people).where(people.c.id == ids["pid"]))


def _p(ids, caps=CAPS):
    return Principal(ids["uid"], "a@e.com", f"U {ids['uid']}", frozenset(caps))


def _launch(ids, template="annual_review"):
    return wsvc.launch(_p(ids), template, actor_user_id=ids["uid"], person_id=ids["pid"])


# --- CRUD + scope ------------------------------------------------------------

def test_launch_list_get():
    ids = _setup()
    try:
        p = _p(ids)
        inst = _launch(ids)
        iid = inst["workflow"]["id"]
        assert inst["workflow"]["status"] == "active" and len(inst["steps"]) >= 1
        assert any(r["id"] == iid for r in wsvc.list_instances(p)["rows"])
        assert wsvc.get_instance(p, iid) is not None
    finally:
        _teardown(ids)


def test_templates_reuse_engine():
    codes = {t["code"] for t in wsvc.templates()}
    assert {"annual_review", "client_onboarding", "tax_preparation"} <= codes


def test_scope_blocks_stranger():
    ids = _setup()
    try:
        iid = _launch(ids)["workflow"]["id"]
        stranger = Principal(99994001, "s@e", "S", {"workflow.view"})
        assert wsvc.get_instance(stranger, iid) is None
        assert all(r["id"] != iid for r in wsvc.list_instances(stranger)["rows"])
    finally:
        _teardown(ids)


# --- execution + dependencies ------------------------------------------------

def test_step_completion_advances_dependency():
    ids = _setup()
    try:
        p = _p(ids)
        inst = _launch(ids)
        active = [s for s in inst["steps"] if s["status"] == "active"]
        assert len(active) == 1
        # Ensure the active step doesn't require approval for this test.
        with engine.begin() as c:
            c.execute(update(workflow_steps).where(workflow_steps.c.id == active[0]["id"])
                      .values(requires_approval=False))
        after = wsvc.complete_step(p, active[0]["id"], actor_user_id=ids["uid"])
        # The completed step is done and a next step has activated (dependency resolution).
        statuses = {s["id"]: s["status"] for s in after["steps"]}
        assert statuses[active[0]["id"]] == "completed"
        assert "active" in statuses.values()   # next step activated
    finally:
        _teardown(ids)


def test_manual_approval_gate():
    ids = _setup()
    try:
        p = _p(ids)
        inst = _launch(ids)
        active = [s for s in inst["steps"] if s["status"] == "active"][0]
        with engine.begin() as c:   # force an approval gate on the active step
            c.execute(update(workflow_steps).where(workflow_steps.c.id == active["id"])
                      .values(requires_approval=True))
        with pytest.raises(wsvc.WorkflowError):
            wsvc.complete_step(p, active["id"], actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


# --- retry + assignment ------------------------------------------------------

def test_retry_budget_and_exhaustion():
    ids = _setup()
    try:
        p = _p(ids)
        inst = _launch(ids)
        sid = [s for s in inst["steps"] if s["status"] == "active"][0]["id"]
        with pytest.raises(wsvc.WorkflowError):        # no budget yet
            wsvc.retry_step(p, sid, actor_user_id=ids["uid"])
        with engine.begin() as c:
            c.execute(update(workflow_steps).where(workflow_steps.c.id == sid).values(max_retries=1))
        assert wsvc.retry_step(p, sid, actor_user_id=ids["uid"])["retry_count"] == 1
        with pytest.raises(wsvc.WorkflowError):        # budget exhausted
            wsvc.retry_step(p, sid, actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


def test_assign_step():
    ids = _setup()
    try:
        p = _p(ids)
        sid = [s for s in _launch(ids)["steps"] if s["status"] == "active"][0]["id"]
        step = wsvc.assign_step(p, sid, ids["uid"], actor_user_id=ids["uid"])
        assert step["assigned_user_id"] == ids["uid"]
        with pytest.raises(wsvc.WorkflowError):
            wsvc.assign_step(p, sid, 99999999, actor_user_id=ids["uid"])   # not a user
    finally:
        _teardown(ids)


# --- lifecycle ---------------------------------------------------------------

def test_pause_resume_cancel():
    ids = _setup()
    try:
        p = _p(ids)
        iid = _launch(ids)["workflow"]["id"]
        assert wsvc.transition(p, iid, "pause", actor_user_id=ids["uid"])["workflow"]["status"] == "paused"
        assert wsvc.transition(p, iid, "resume", actor_user_id=ids["uid"])["workflow"]["status"] == "active"
        assert wsvc.transition(p, iid, "cancel", actor_user_id=ids["uid"])["workflow"]["status"] == "cancelled"
    finally:
        _teardown(ids)


# --- triggers ----------------------------------------------------------------

def test_trigger_config_validation_and_fire():
    ids = _setup()
    try:
        p = _p(ids)
        with pytest.raises(wtrig.TriggerError):
            wtrig.configure_trigger(p, name=f"t-{ids['tag']}", event_type="not_a_trigger",
                                    template_code="annual_review", actor_user_id=ids["uid"])
        with pytest.raises(wtrig.TriggerError):
            wtrig.configure_trigger(p, name=f"t-{ids['tag']}", event_type="opportunity_won",
                                    template_code="no_such_template", actor_user_id=ids["uid"])
        wtrig.configure_trigger(p, name=f"t-{ids['tag']}", event_type="opportunity_won",
                                template_code="client_onboarding", actor_user_id=ids["uid"], active=True)
        launched = wtrig.fire("opportunity_won", entity_type="person", entity_id=ids["pid"],
                              actor_user_id=ids["uid"], payload={}, idempotency_key=f"e-{ids['tag']}")
        assert len(launched) >= 1
    finally:
        _teardown(ids)


def test_fire_is_failure_isolated():
    ids = _setup()
    try:
        # Unknown trigger type -> no-op, never raises.
        assert wtrig.fire("bogus_event", entity_type="person", entity_id=ids["pid"],
                          actor_user_id=ids["uid"], payload={}, idempotency_key="x") == []
    finally:
        _teardown(ids)


# --- actions -----------------------------------------------------------------

def test_action_registry():
    assert set(wact.list_actions()) >= {"timeline_event", "document_relationship", "assign", "notification"}
    with pytest.raises(wact.ActionError):
        wact.execute_action("nope", context={}, actor_user_id=1)


def test_action_timeline_event():
    ids = _setup()
    try:
        from sqlalchemy import func

        from app.db import timeline_events
        wact.execute_action("timeline_event", actor_user_id=ids["uid"], context={
            "person_id": ids["pid"], "title": "WF action", "external_id": f"wfa-{ids['tag']}"})
        with engine.connect() as c:
            n = c.scalar(select(func.count()).select_from(timeline_events).where(
                timeline_events.c.external_id == f"wfa-{ids['tag']}"))
        assert n == 1
        with engine.begin() as c:
            c.execute(delete(timeline_events).where(timeline_events.c.external_id == f"wfa-{ids['tag']}"))
    finally:
        _teardown(ids)


# --- analytics + audit -------------------------------------------------------

def test_analytics_active_workflows_metric():
    ids = _setup()
    try:
        _launch(ids)
        from app.services.analytics import metrics
        p = Principal(ids["uid"], "a@e", "A", frozenset({"analytics.view"}))
        m = metrics.compute_metric(p, "active_workflows")
        assert m["value"] >= 1 and m["category"] == "operations"
    finally:
        _teardown(ids)


def test_audit_history():
    ids = _setup()
    try:
        p = _p(ids)
        iid = _launch(ids)["workflow"]["id"]
        wsvc.transition(p, iid, "pause", actor_user_id=ids["uid"])
        hist = wsvc.audit_history(p, iid)
        assert any(e["event_type"] == "workflow_pause" for e in hist)
    finally:
        _teardown(ids)


# --- routes + dependency direction -------------------------------------------

def test_overview_route_renders():
    from app.routes.workflow_automation import overview
    ids = _setup()
    try:
        req = Request({"type": "http", "method": "GET", "path": "/workflow-automation",
                       "headers": [], "query_string": b""})
        resp = overview(req, principal=_p(ids))
        assert resp.status_code == 200 and "Workflow Automation" in resp.body.decode()
    finally:
        _teardown(ids)


def test_dependency_direction():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app" / "services"
    # The base engine must NOT import the orchestration layer (one-way: orchestration -> engine).
    assert "workflow_orchestration" not in (root / "workflow_automation.py").read_text()
    # Advisor intelligence untouched.
    assert "workflow_orchestration" not in (root / "advisor_intelligence.py").read_text()
    # Orchestration must not import analytics (Workflow never depends on Analytics).
    for module in ("service.py", "triggers.py", "actions.py"):
        src = (root / "workflow_orchestration" / module).read_text()
        assert not re.search(r"from\s+app\.services\.analytics", src)
