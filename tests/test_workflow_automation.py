from datetime import datetime, timedelta, timezone
import uuid
import pytest
from sqlalchemy import func, select

from app.db import (automation_triggers, engine, households, people, roles, user_roles,
    users, work_approvals, workflow_escalations, workflow_events, workflow_instances,
    workflow_steps, workflow_templates)
from app.main import app
from app.security.models import Principal
from app.services.workflow_automation import (complete_step, decide_approval, evaluate_sla,
    execute_automation_action, launch_workflow, list_templates, process_event, request_approval, transition_workflow,
    workflow_detail)

def _actors():
    suffix = uuid.uuid4().hex[:10]
    with engine.begin() as connection:
        household_id = connection.execute(households.insert().values(name=f"Workflow {suffix}").returning(households.c.id)).scalar_one()
        person_id = connection.execute(people.insert().values(household_id=household_id, full_name=f"Workflow Person {suffix}", active=True).returning(people.c.id)).scalar_one()
        role_id = connection.scalar(select(roles.c.id).where(roles.c.code == "administrator"))
        actor_ids = []
        for label in ("requester", "approver"):
            actor_id = connection.execute(users.insert().values(email=f"{label}-{suffix}@example.com", normalized_email=f"{label}-{suffix}@example.com", display_name=label, auth_subject=f"{label}-{suffix}", status="active").returning(users.c.id)).scalar_one()
            connection.execute(user_roles.insert().values(user_id=actor_id, role_id=role_id)); actor_ids.append(actor_id)
    return household_id, person_id, actor_ids

def test_seeded_templates_are_versioned_and_complete():
    templates = list_templates()
    assert len({row["code"] for row in templates}) >= 12
    assert all(row["version"] == 1 and row["status"] == "published" for row in templates)

def test_launch_is_idempotent_and_dependencies_activate_sequentially():
    household_id, person_id, actors = _actors(); key = f"launch-{uuid.uuid4()}"
    instance_id = launch_workflow("client_onboarding", actor_user_id=actors[0], person_id=person_id, household_id=household_id, idempotency_key=key)
    assert launch_workflow("client_onboarding", actor_user_id=actors[0], person_id=person_id, idempotency_key=key) == instance_id
    data = workflow_detail(instance_id)
    assert [step["status"] for step in data["steps"]] == ["active", "pending", "pending", "pending"]
    complete_step(data["steps"][0]["id"], actor_user_id=actors[0])
    assert [step["status"] for step in workflow_detail(instance_id)["steps"]][:2] == ["completed", "active"]
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(workflow_instances).where(workflow_instances.c.idempotency_key == key)) == 1
        assert connection.scalar(select(func.count()).select_from(workflow_events).where(workflow_events.c.workflow_instance_id == instance_id, workflow_events.c.event_type == "workflow_launched")) == 1

def test_lifecycle_state_machine_pause_resume_cancel_and_reopen():
    household_id, person_id, actors = _actors()
    instance_id = launch_workflow("annual_review", actor_user_id=actors[0], person_id=person_id, idempotency_key=f"lifecycle-{uuid.uuid4()}")
    assert transition_workflow(instance_id, "pause", actor_user_id=actors[0]) == "paused"
    assert transition_workflow(instance_id, "resume", actor_user_id=actors[0]) == "active"
    assert transition_workflow(instance_id, "cancel", actor_user_id=actors[0]) == "cancelled"
    assert transition_workflow(instance_id, "reopen", actor_user_id=actors[0]) == "active"
    with pytest.raises(ValueError): transition_workflow(instance_id, "resume", actor_user_id=actors[0])

def test_workflow_detail_enforces_record_scope():
    _, person_id, actors = _actors()
    instance_id = launch_workflow("annual_review", actor_user_id=actors[0], person_id=person_id, idempotency_key=f"scope-{uuid.uuid4()}")
    unauthorized = Principal(999999, "none@example.com", "None", frozenset({"work.read"}))
    with pytest.raises(PermissionError): workflow_detail(instance_id, unauthorized)
    administrator = Principal(999998, "admin@example.com", "Admin", frozenset({"work.read", "record.read_all"}))
    assert workflow_detail(instance_id, administrator)["workflow"]["id"] == instance_id

def test_independent_approval_enforces_segregation_of_duties():
    _, person_id, actors = _actors()
    instance_id = launch_workflow("compliance_review", actor_user_id=actors[0], person_id=person_id, idempotency_key=f"approval-{uuid.uuid4()}")
    steps = workflow_detail(instance_id)["steps"]
    complete_step(steps[0]["id"], actor_user_id=actors[0]); complete_step(steps[1]["id"], actor_user_id=actors[0])
    approval_step = workflow_detail(instance_id)["steps"][2]
    with pytest.raises(ValueError): request_approval(approval_step["id"], requested_by_user_id=actors[0], approver_user_id=actors[0])
    approval_id = request_approval(approval_step["id"], requested_by_user_id=actors[0], approver_user_id=actors[1])
    with pytest.raises(ValueError): decide_approval(approval_id, approver_user_id=actors[0], decision="approved")
    decide_approval(approval_id, approver_user_id=actors[1], decision="approved", notes="Independent review complete")
    complete_step(approval_step["id"], actor_user_id=actors[0])
    with engine.connect() as connection:
        assert connection.scalar(select(work_approvals.c.status).where(work_approvals.c.id == approval_id)) == "approved"

def test_event_trigger_and_sla_escalation_are_idempotent():
    _, person_id, actors = _actors(); suffix = uuid.uuid4().hex
    with engine.begin() as connection:
        connection.execute(automation_triggers.insert().values(name=f"New document {suffix}", event_type="document.matched", entity_type="person", conditions={"source": "microsoft"}, template_code="annual_review", priority=10))
    ids = process_event("document.matched", "person", person_id, {"source": "microsoft"}, actor_user_id=actors[0], idempotency_key=suffix)
    assert ids == process_event("document.matched", "person", person_id, {"source": "microsoft"}, actor_user_id=actors[0], idempotency_key=suffix)
    first_step = workflow_detail(ids[0])["steps"][0]
    with engine.begin() as connection:
        connection.execute(workflow_steps.update().where(workflow_steps.c.id == first_step["id"]).values(sla_due_at=datetime.now(timezone.utc) - timedelta(hours=1)))
    created = evaluate_sla(); assert len(created) == 1 and evaluate_sla() == []
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(workflow_escalations).where(workflow_escalations.c.workflow_step_id == first_step["id"])) == 1

def test_automation_action_ledger_prevents_duplicate_side_effects():
    _, person_id, actors = _actors(); key = f"action-{uuid.uuid4()}"
    instance_id = launch_workflow("annual_review", actor_user_id=actors[0], person_id=person_id, idempotency_key=f"instance-{key}")
    first = execute_automation_action(instance_id, "publish_timeline", payload={"title": "Review started"}, idempotency_key=key)
    assert execute_automation_action(instance_id, "publish_timeline", payload={"title": "Review started"}, idempotency_key=key) == first

def test_workflow_ui_and_versioned_api_routes_are_registered():
    routes = {(route.path, method) for route in app.routes for method in (getattr(route, "methods", None) or set())}
    assert {( "/workflows", "GET"), ("/api/v1/workflows/templates", "GET"), ("/api/v1/workflows", "POST"), ("/api/v1/workflows/{instance_id}/pause", "POST"), ("/api/v1/workflows/steps/{step_id}/approvals", "POST"), ("/api/v1/workflows/approvals/{approval_id}/decision", "POST"), ("/api/v1/workflows/events", "POST"), ("/api/v1/workflows/metrics", "GET")} <= routes
