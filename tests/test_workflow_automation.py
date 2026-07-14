from datetime import datetime, timedelta, timezone
import uuid
import pytest
from sqlalchemy import func, select

from app.db import (audit_events, automation_triggers, engine, households, people, roles,
    timeline_events, user_roles, users, work_approvals, workflow_escalations,
    workflow_events, workflow_instances, workflow_step_dependencies, workflow_steps,
    workflow_template_steps, workflow_templates)
from app.main import app
from app.security.models import Principal
from app.services.workflow_automation import (complete_step, decide_approval, evaluate_sla,
    execute_automation_action, launch_workflow, list_templates, process_event, request_approval, transition_workflow,
    workflow_detail)
from app.services.work_management import assign_work, dashboard

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

def _publish_graph_template(suffix):
    with engine.begin() as connection:
        template_id = connection.execute(workflow_templates.insert().values(code=f"graph-{suffix}", version=1, name="Graph validation", category="validation", status="draft", default_sla_hours=24).returning(workflow_templates.c.id)).scalar_one()
        ids = {}
        definitions = [("root", 10, "sequential", {}, None), ("parallel_a", 20, "parallel", {}, "waiting_on_client"), ("parallel_b", 21, "parallel", {}, None), ("conditional", 22, "parallel", {"requires_tax": True}, None), ("finish", 30, "sequential", {}, None)]
        for key, sequence, mode, condition, queue in definitions:
            ids[key] = connection.execute(workflow_template_steps.insert().values(template_id=template_id, step_key=key, name=key.replace("_", " ").title(), sequence=sequence, execution_mode=mode, condition=condition, queue_code=queue).returning(workflow_template_steps.c.id)).scalar_one()
        connection.execute(workflow_step_dependencies.insert(), [{"step_id": ids["parallel_a"], "depends_on_step_id": ids["root"]}, {"step_id": ids["parallel_b"], "depends_on_step_id": ids["root"]}, {"step_id": ids["conditional"], "depends_on_step_id": ids["root"]}, {"step_id": ids["finish"], "depends_on_step_id": ids["parallel_a"]}, {"step_id": ids["finish"], "depends_on_step_id": ids["parallel_b"]}, {"step_id": ids["finish"], "depends_on_step_id": ids["conditional"]}])
        connection.execute(workflow_templates.update().where(workflow_templates.c.id == template_id).values(status="published", published_at=datetime.now(timezone.utc)))
    return template_id, ids

def test_published_templates_are_immutable_and_versions_are_independent():
    suffix = uuid.uuid4().hex[:8]; template_id, ids = _publish_graph_template(suffix)
    with pytest.raises(Exception):
        with engine.begin() as connection: connection.execute(workflow_templates.update().where(workflow_templates.c.id == template_id).values(name="Mutated"))
    with pytest.raises(Exception):
        with engine.begin() as connection: connection.execute(workflow_template_steps.update().where(workflow_template_steps.c.id == ids["root"]).values(name="Mutated"))
    with engine.begin() as connection:
        version_two = connection.execute(workflow_templates.insert().values(code=f"graph-{suffix}", version=2, name="Graph validation v2", category="validation", status="draft").returning(workflow_templates.c.id)).scalar_one()
    assert version_two != template_id

def test_multi_hop_circular_dependencies_are_rejected():
    suffix = uuid.uuid4().hex[:8]
    with engine.begin() as connection:
        template_id = connection.execute(workflow_templates.insert().values(code=f"cycle-{suffix}", version=1, name="Cycle", category="validation", status="draft").returning(workflow_templates.c.id)).scalar_one()
        ids = [connection.execute(workflow_template_steps.insert().values(template_id=template_id, step_key=f"s{i}", name=f"Step {i}", sequence=i).returning(workflow_template_steps.c.id)).scalar_one() for i in range(3)]
        connection.execute(workflow_step_dependencies.insert().values(step_id=ids[1], depends_on_step_id=ids[0]))
        connection.execute(workflow_step_dependencies.insert().values(step_id=ids[2], depends_on_step_id=ids[1]))
    with pytest.raises(Exception):
        with engine.begin() as connection: connection.execute(workflow_step_dependencies.insert().values(step_id=ids[0], depends_on_step_id=ids[2]))

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

def test_execution_snapshot_parallel_conditions_queue_assignment_timeline_and_audit():
    household_id, person_id, actors = _actors(); suffix = uuid.uuid4().hex[:8]
    _publish_graph_template(suffix)
    instance_id = launch_workflow(f"graph-{suffix}", actor_user_id=actors[0], person_id=person_id, household_id=household_id, context={"requires_tax": False}, idempotency_key=f"graph-launch-{suffix}")
    data = workflow_detail(instance_id); steps = {step["name"]: step for step in data["steps"]}
    assert data["workflow"]["template_snapshot"]["code"] == f"graph-{suffix}"
    assert steps["Conditional"]["status"] == "skipped" and steps["Conditional"]["condition_result"] is False
    assert steps["Parallel A"]["definition_snapshot"]["execution_mode"] == "parallel"
    complete_step(steps["Root"]["id"], actor_user_id=actors[0])
    active = {step["name"] for step in workflow_detail(instance_id)["steps"] if step["status"] == "active"}
    assert active == {"Parallel A", "Parallel B"}
    assert next(step for step in workflow_detail(instance_id)["steps"] if step["name"] == "Parallel A")["waiting_on"] == "client"
    assignment_id = assign_work(entity_type="workflow_instance", entity_id=instance_id, assignment_role="primary", user_id=actors[0], actor_user_id=actors[0], request_id=f"assign-{suffix}")
    principal = Principal(actors[0], "requester@example.com", "Requester", frozenset({"work.read"}))
    assert any(item["workflow_name"] == "Graph validation" for item in dashboard(principal)["items"])
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(timeline_events).where(timeline_events.c.person_id == person_id, timeline_events.c.source == "workflow_automation")) >= 1
        audit_id = connection.scalar(select(audit_events.c.id).where(audit_events.c.entity_type == "workflow_instance", audit_events.c.entity_id == str(instance_id)))
    assert assignment_id and audit_id
    with pytest.raises(Exception):
        with engine.begin() as connection: connection.execute(audit_events.update().where(audit_events.c.id == audit_id).values(action="tampered"))

def test_lifecycle_state_machine_pause_resume_cancel_and_reopen():
    household_id, person_id, actors = _actors()
    instance_id = launch_workflow("annual_review", actor_user_id=actors[0], person_id=person_id, idempotency_key=f"lifecycle-{uuid.uuid4()}")
    assert transition_workflow(instance_id, "pause", actor_user_id=actors[0]) == "paused"
    assert transition_workflow(instance_id, "resume", actor_user_id=actors[0]) == "active"
    assert transition_workflow(instance_id, "complete", actor_user_id=actors[0]) == "completed"
    assert transition_workflow(instance_id, "reopen", actor_user_id=actors[0]) == "active"
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
    schema = app.openapi()
    assert "post" in schema["paths"]["/api/v1/workflows"] and "get" in schema["paths"]["/api/v1/workflows/{instance_id}"]
    from app.routes.workflows import templates
    rendered = templates.get_template("workflows/index.html").render(templates=[], metrics={"by_status": {}, "pending_approvals": 0, "open_escalations": 0}, principal=None)
    assert "Workflow Automation" in rendered
