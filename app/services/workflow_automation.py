import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select

from app.db import (
    automation_actions,
    automation_triggers,
    engine,
    timeline_events,
    work_approvals,
    work_queues,
    workflow_escalations,
    workflow_events,
    workflow_instances,
    workflow_step_dependencies,
    workflow_steps,
    workflow_template_steps,
    workflow_templates,
)
from app.platform.workflow_approval_state import (
    check_assigned_approver,
    check_decider_not_requester,
    check_independent_requester,
    validate_decidable,
    validate_decision,
    validate_reassignable,
)
from app.platform.workflow_events import emit_approval_event, emit_transition_event
from app.platform.workflow_state_machine import (
    ACTIVE_STEP_STATES,
    dependencies_satisfied,
    validate_transition,
)
from app.security.audit import write_audit_event
from app.services.timeline import add_timeline_event


def _event(connection, instance_id, event_type, actor_user_id=None, step_id=None, payload=None, key=None):
    key = key or f"workflow:{instance_id}:{event_type}:{uuid.uuid4().hex}"
    existing = connection.scalar(select(workflow_events.c.id).where(workflow_events.c.idempotency_key == key))
    if existing: return existing
    return connection.execute(workflow_events.insert().values(workflow_instance_id=instance_id,
        workflow_step_id=step_id, event_type=event_type, idempotency_key=key,
        actor_user_id=actor_user_id, payload=payload or {}).returning(workflow_events.c.id)).scalar_one()

def _publish(instance, event_type, title, metadata):
    if instance["person_id"] or instance["household_id"]:
        add_timeline_event(source="workflow_automation", event_type=event_type, title=title,
            person_id=instance["person_id"], household_id=instance["household_id"],
            external_id=f"workflow-{instance['id']}-{event_type}-{uuid.uuid4().hex}", event_metadata=metadata)

def list_templates(include_drafts=False):
    with engine.connect() as connection:
        query = select(workflow_templates).order_by(workflow_templates.c.code, workflow_templates.c.version.desc())
        if not include_drafts: query = query.where(workflow_templates.c.status == "published")
        return connection.execute(query).mappings().all()

def _matches(condition, context):
    return all(context.get(key) == value for key, value in (condition or {}).items())

def launch_workflow(template_code, *, actor_user_id, person_id=None, household_id=None,
                    version=None, priority="normal", context=None, idempotency_key=None, request_id=None):
    context = context or {}; idempotency_key = idempotency_key or f"manual:{uuid.uuid4().hex}"
    with engine.begin() as connection:
        existing = connection.scalar(select(workflow_instances.c.id).where(workflow_instances.c.idempotency_key == idempotency_key))
        if existing: return existing
        query = select(workflow_templates).where(workflow_templates.c.code == template_code, workflow_templates.c.status == "published")
        query = query.where(workflow_templates.c.version == version) if version else query.order_by(workflow_templates.c.version.desc()).limit(1)
        template = connection.execute(query).mappings().one_or_none()
        if not template: raise ValueError("Published workflow template not found")
        template_snapshot = {key: template[key] for key in ("code", "version", "name", "category", "description", "default_sla_hours", "trigger_config")}
        instance_id = connection.execute(workflow_instances.insert().values(name=template["name"], workflow_type=template["category"], person_id=person_id, household_id=household_id, status="active", priority=priority, metadata=context, template_id=template["id"], template_version=template["version"], template_snapshot=template_snapshot, launched_by_user_id=actor_user_id, idempotency_key=idempotency_key).returning(workflow_instances.c.id)).scalar_one()
        definitions = connection.execute(select(workflow_template_steps).where(workflow_template_steps.c.template_id == template["id"]).order_by(workflow_template_steps.c.sequence)).mappings().all()
        definition_ids = {row["id"] for row in definitions}
        dependent = set(connection.scalars(select(workflow_step_dependencies.c.step_id).where(workflow_step_dependencies.c.step_id.in_(definition_ids)))) if definition_ids else set()
        now = datetime.now(UTC)
        for definition in definitions:
            condition_result = _matches(definition["condition"], context)
            active = definition["id"] not in dependent and condition_result
            queue_criteria = connection.execute(select(work_queues.c.criteria).where(work_queues.c.code == definition["queue_code"])).scalar_one_or_none() if definition["queue_code"] else {}
            snapshot = {key: definition[key] for key in ("step_key", "name", "sequence", "step_type", "execution_mode", "condition", "assignment_config", "queue_code", "sla_hours", "requires_independent_approval", "automation_action", "configuration")}
            connection.execute(workflow_steps.insert().values(workflow_instance_id=instance_id, name=definition["name"], sequence=definition["sequence"], status="active" if active else ("skipped" if not condition_result else "pending"), priority=priority, waiting_on=(queue_criteria or {}).get("waiting_on"), sla_due_at=now + timedelta(hours=definition["sla_hours"] or template["default_sla_hours"] or 120) if active else None, requires_approval=definition["step_type"] == "approval", template_step_id=definition["id"], definition_snapshot=snapshot, activated_at=now if active else None, condition_result=condition_result))
        launched_event_id = _event(connection, instance_id, "workflow_launched", actor_user_id, payload={"template": template_code, "version": template["version"]}, key=f"{idempotency_key}:launch")
        emit_transition_event(connection, instance_id=instance_id, action="launch", domain_event_id=launched_event_id, actor_user_id=actor_user_id, correlation_id=f"workflow_instance:{instance_id}", payload_extra={"template": template_code, "version": template["version"]})
        instance = connection.execute(select(workflow_instances).where(workflow_instances.c.id == instance_id)).mappings().one()
    _publish(instance, "workflow_launched", f"{template['name']} workflow launched", {"template": template_code})
    write_audit_event(action="workflow.launched", entity_type="workflow_instance", entity_id=instance_id, actor_user_id=actor_user_id, request_id=request_id or idempotency_key, metadata={"template": template_code})
    return instance_id

def transition_workflow(instance_id, action, *, actor_user_id, reason=None, request_id=None):
    now = datetime.now(UTC)
    with engine.begin() as connection:
        instance = connection.execute(select(workflow_instances).where(workflow_instances.c.id == instance_id).with_for_update()).mappings().one_or_none()
        if not instance: raise ValueError("Workflow not found")
        target = validate_transition(instance["status"], action)
        values = {"status": target, "status_reason": reason, "updated_at": now}
        if action == "pause": values["paused_at"] = now
        if action == "cancel": values["cancelled_at"] = now
        if action == "complete": values["completed_at"] = now
        if action in {"resume", "reopen"}: values.update(paused_at=None, cancelled_at=None, completed_at=None)
        connection.execute(workflow_instances.update().where(workflow_instances.c.id == instance_id).values(**values))
        if action == "pause": connection.execute(workflow_steps.update().where(workflow_steps.c.workflow_instance_id == instance_id, workflow_steps.c.status == "active").values(status="paused", paused_at=now))
        if action in {"resume", "reopen"}: connection.execute(workflow_steps.update().where(workflow_steps.c.workflow_instance_id == instance_id, workflow_steps.c.status == "paused").values(status="active", paused_at=None))
        if action == "cancel": connection.execute(workflow_steps.update().where(workflow_steps.c.workflow_instance_id == instance_id, workflow_steps.c.status.in_(ACTIVE_STEP_STATES)).values(status="cancelled", cancelled_at=now))
        transition_event_id = _event(connection, instance_id, f"workflow_{action}", actor_user_id, payload={"reason": reason})
        emit_transition_event(connection, instance_id=instance_id, action=action, domain_event_id=transition_event_id, actor_user_id=actor_user_id, payload_extra={"from": instance["status"], "to": target}, metadata_extra={"reason": reason} if reason else None)
    _publish(instance, f"workflow_{action}", f"Workflow {action}d", {"reason": reason})
    write_audit_event(action=f"workflow.{action}", entity_type="workflow_instance", entity_id=instance_id, actor_user_id=actor_user_id, request_id=request_id or f"workflow-{uuid.uuid4()}", metadata={"reason": reason})
    return target

def complete_step(step_id, *, actor_user_id, request_id=None):
    now = datetime.now(UTC)
    with engine.begin() as connection:
        step = connection.execute(select(workflow_steps).where(workflow_steps.c.id == step_id).with_for_update()).mappings().one_or_none()
        if not step or step["status"] != "active": raise ValueError("Only an active step can be completed")
        if step["requires_approval"]:
            approved = connection.scalar(select(work_approvals.c.id).where(work_approvals.c.workflow_step_id == step_id, work_approvals.c.status == "approved"))
            if not approved: raise ValueError("Step requires an approved independent review")
        connection.execute(workflow_steps.update().where(workflow_steps.c.id == step_id).values(status="completed", completed_at=now))
        _event(connection, step["workflow_instance_id"], "step_completed", actor_user_id, step_id, key=f"step:{step_id}:completed")
        pending = connection.execute(select(workflow_steps).where(workflow_steps.c.workflow_instance_id == step["workflow_instance_id"], workflow_steps.c.status == "pending")).mappings().all()
        completed_template_ids = set(connection.scalars(select(workflow_steps.c.template_step_id).where(workflow_steps.c.workflow_instance_id == step["workflow_instance_id"], workflow_steps.c.status.in_(("completed", "skipped")))))
        for candidate in pending:
            dependencies = set(connection.scalars(select(workflow_step_dependencies.c.depends_on_step_id).where(workflow_step_dependencies.c.step_id == candidate["template_step_id"])))
            if dependencies_satisfied(dependencies, completed_template_ids):
                definition = connection.execute(select(workflow_template_steps).where(workflow_template_steps.c.id == candidate["template_step_id"])).mappings().one()
                connection.execute(workflow_steps.update().where(workflow_steps.c.id == candidate["id"]).values(status="active", activated_at=now, sla_due_at=now + timedelta(hours=definition["sla_hours"] or 120)))
        remaining = connection.scalar(select(func.count()).select_from(workflow_steps).where(workflow_steps.c.workflow_instance_id == step["workflow_instance_id"], workflow_steps.c.status.in_(ACTIVE_STEP_STATES)))
        if not remaining: connection.execute(workflow_instances.update().where(workflow_instances.c.id == step["workflow_instance_id"]).values(status="completed", completed_at=now))

def request_approval(step_id, *, requested_by_user_id, approver_user_id=None, approver_team_id=None, due_at=None, request_id=None):
    if not approver_user_id and not approver_team_id: raise ValueError("Approver user or team is required")
    check_independent_requester(requested_by_user_id, approver_user_id)
    with engine.begin() as connection:
        step = connection.execute(select(workflow_steps).where(workflow_steps.c.id == step_id)).mappings().one_or_none()
        if not step: raise ValueError("Workflow step not found")
        approval_id = connection.execute(work_approvals.insert().values(entity_type="workflow_step", entity_id=step_id, approval_type="independent", workflow_step_id=step_id, requested_by_user_id=requested_by_user_id, approver_user_id=approver_user_id, approver_team_id=approver_team_id, due_at=due_at, requires_independent_approver=True).returning(work_approvals.c.id)).scalar_one()
        event_id = _event(connection, step["workflow_instance_id"], "approval_requested", requested_by_user_id, step_id, {"approval_id": approval_id})
        emit_approval_event(connection, kind="requested", instance_id=step["workflow_instance_id"], approval_id=approval_id, domain_event_id=event_id, step_id=step_id, actor_user_id=requested_by_user_id, payload_extra={"approver_user_id": approver_user_id, "approver_team_id": approver_team_id})
        instance_id = step["workflow_instance_id"]
    write_audit_event(action="workflow.approval.requested", entity_type="work_approval", entity_id=approval_id, actor_user_id=requested_by_user_id, request_id=request_id or f"approval-{uuid.uuid4()}", metadata={"workflow_instance_id": instance_id, "workflow_step_id": step_id})
    return approval_id

def decide_approval(approval_id, *, approver_user_id, decision, notes=None, request_id=None):
    validate_decision(decision)
    with engine.begin() as connection:
        approval = connection.execute(select(work_approvals).where(work_approvals.c.id == approval_id).with_for_update()).mappings().one_or_none()
        validate_decidable(approval)
        check_decider_not_requester(approval["requested_by_user_id"], approver_user_id)
        check_assigned_approver(approval["approver_user_id"], approver_user_id)
        connection.execute(work_approvals.update().where(work_approvals.c.id == approval_id).values(status=decision, approver_user_id=approver_user_id, decided_at=datetime.now(UTC), decision_notes=notes))
        step = connection.execute(select(workflow_steps).where(workflow_steps.c.id == approval["workflow_step_id"])).mappings().one()
        event_id = _event(connection, step["workflow_instance_id"], f"approval_{decision}", approver_user_id, step["id"], {"approval_id": approval_id})
        emit_approval_event(connection, kind="decided", instance_id=step["workflow_instance_id"], approval_id=approval_id, domain_event_id=event_id, step_id=step["id"], actor_user_id=approver_user_id, payload_extra={"decision": decision})
        instance_id, decided_step_id = step["workflow_instance_id"], step["id"]
    write_audit_event(action="workflow.approval.decided", entity_type="work_approval", entity_id=approval_id, actor_user_id=approver_user_id, request_id=request_id or f"approval-{uuid.uuid4()}", metadata={"decision": decision, "workflow_instance_id": instance_id, "workflow_step_id": decided_step_id})

def reassign_approval(approval_id, *, reassigned_by_user_id, new_approver_user_id=None, new_approver_team_id=None, reason=None, request_id=None):
    """Reassign a pending approval to a new approver/team (SoD-checked). Deterministic;
    never changes workflow state. History is preserved in the append-only event ledger."""
    if not new_approver_user_id and not new_approver_team_id: raise ValueError("New approver user or team is required")
    with engine.begin() as connection:
        approval = connection.execute(select(work_approvals).where(work_approvals.c.id == approval_id).with_for_update()).mappings().one_or_none()
        validate_reassignable(approval)
        check_independent_requester(approval["requested_by_user_id"], new_approver_user_id)
        old_approver = approval["approver_user_id"]
        connection.execute(work_approvals.update().where(work_approvals.c.id == approval_id).values(approver_user_id=new_approver_user_id, approver_team_id=new_approver_team_id))
        step = connection.execute(select(workflow_steps).where(workflow_steps.c.id == approval["workflow_step_id"])).mappings().one()
        event_id = _event(connection, step["workflow_instance_id"], "approval_reassigned", reassigned_by_user_id, step["id"], {"approval_id": approval_id, "from_approver": old_approver, "to_approver": new_approver_user_id})
        emit_approval_event(connection, kind="reassigned", instance_id=step["workflow_instance_id"], approval_id=approval_id, domain_event_id=event_id, step_id=step["id"], actor_user_id=reassigned_by_user_id, payload_extra={"from_approver": old_approver, "to_approver": new_approver_user_id}, metadata_extra={"reason": reason} if reason else None)
        instance_id, reassigned_step_id = step["workflow_instance_id"], step["id"]
    write_audit_event(action="workflow.approval.reassigned", entity_type="work_approval", entity_id=approval_id, actor_user_id=reassigned_by_user_id, request_id=request_id or f"approval-{uuid.uuid4()}", metadata={"from_approver": old_approver, "to_approver": new_approver_user_id, "workflow_instance_id": instance_id, "workflow_step_id": reassigned_step_id})
    return approval_id

def process_event(event_type, entity_type, entity_id, payload, *, actor_user_id, idempotency_key):
    with engine.connect() as connection:
        rules = connection.execute(select(automation_triggers).where(automation_triggers.c.active.is_(True), automation_triggers.c.event_type == event_type, or_(automation_triggers.c.entity_type.is_(None), automation_triggers.c.entity_type == entity_type)).order_by(automation_triggers.c.priority)).mappings().all()
    launched = []
    for rule in rules:
        if _matches(rule["conditions"], payload):
            launched.append(launch_workflow(rule["template_code"], actor_user_id=actor_user_id, person_id=entity_id if entity_type == "person" else None, household_id=entity_id if entity_type == "household" else None, context=payload, idempotency_key=f"event:{idempotency_key}:trigger:{rule['id']}"))
    return launched

def evaluate_sla(now=None):
    now = now or datetime.now(UTC); created = []
    with engine.begin() as connection:
        overdue = connection.execute(select(workflow_steps).where(workflow_steps.c.status == "active", workflow_steps.c.sla_due_at < now)).mappings().all()
        for step in overdue:
            existing = connection.scalar(select(workflow_escalations.c.id).where(workflow_escalations.c.workflow_step_id == step["id"], workflow_escalations.c.escalation_type == "sla_breach", workflow_escalations.c.level == 1))
            if not existing:
                created.append(connection.execute(workflow_escalations.insert().values(workflow_instance_id=step["workflow_instance_id"], workflow_step_id=step["id"], escalation_type="sla_breach", level=1, due_at=step["sla_due_at"], metadata={"step": step["name"]}).returning(workflow_escalations.c.id)).scalar_one())
                _event(connection, step["workflow_instance_id"], "sla_escalated", step_id=step["id"], key=f"step:{step['id']}:sla:1")
    return created

def execute_automation_action(instance_id, action_type, *, step_id=None, payload=None, idempotency_key):
    """Execute a bounded internal action once; provider calls belong in adapters."""
    payload = payload or {}
    with engine.begin() as connection:
        existing = connection.execute(select(automation_actions).where(automation_actions.c.idempotency_key == idempotency_key)).mappings().one_or_none()
        if existing: return existing["id"]
        action_id = connection.execute(automation_actions.insert().values(workflow_instance_id=instance_id, workflow_step_id=step_id, action_type=action_type, idempotency_key=idempotency_key, status="running", input=payload, attempts=1).returning(automation_actions.c.id)).scalar_one()
        instance = connection.execute(select(workflow_instances).where(workflow_instances.c.id == instance_id)).mappings().one_or_none()
        if not instance: raise ValueError("Workflow not found")
        if action_type == "publish_timeline":
            connection.execute(timeline_events.insert().values(source="workflow_automation", event_type=payload.get("event_type", "workflow_automation"), title=payload.get("title", "Workflow automation"), person_id=instance["person_id"], household_id=instance["household_id"], external_id=f"automation-{idempotency_key}", event_metadata=payload))
            output = {"published": True}
        else:
            raise ValueError("Unsupported automation action; register it through a domain adapter")
        connection.execute(automation_actions.update().where(automation_actions.c.id == action_id).values(status="completed", output=output, executed_at=datetime.now(UTC), updated_at=datetime.now(UTC)))
        _event(connection, instance_id, "automation_completed", step_id=step_id, payload={"action": action_type}, key=f"action:{idempotency_key}:completed")
    return action_id

def workflow_detail(instance_id, principal=None):
    with engine.connect() as connection:
        instance = connection.execute(select(workflow_instances).where(workflow_instances.c.id == instance_id)).mappings().one_or_none()
        if not instance: raise ValueError("Workflow not found")
        if principal is not None and not principal.can("record.read_all"):
            from app.services.work_management import authorized_assignments
            assigned = {(row["entity_type"], row["entity_id"]) for row in authorized_assignments(connection, principal)}
            scope = {("workflow_instance", instance_id), ("person", instance["person_id"]), ("household", instance["household_id"])}
            if not assigned & scope: raise PermissionError("Workflow is outside the authorized record scope")
        return {"workflow": instance, "steps": connection.execute(select(workflow_steps).where(workflow_steps.c.workflow_instance_id == instance_id).order_by(workflow_steps.c.sequence)).mappings().all(), "events": connection.execute(select(workflow_events).where(workflow_events.c.workflow_instance_id == instance_id).order_by(workflow_events.c.occurred_at)).mappings().all()}

def workflow_metrics():
    with engine.connect() as connection:
        rows = connection.execute(select(workflow_instances.c.status, func.count().label("count")).group_by(workflow_instances.c.status)).mappings().all()
        breached = connection.scalar(select(func.count()).select_from(workflow_escalations).where(workflow_escalations.c.status == "open"))
        pending = connection.scalar(select(func.count()).select_from(work_approvals).where(work_approvals.c.status == "pending"))
    return {"by_status": {row["status"]: row["count"] for row in rows}, "open_escalations": breached, "pending_approvals": pending}
