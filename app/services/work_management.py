from datetime import date, datetime, timezone
import uuid
from sqlalchemy import and_, or_, select
import sqlalchemy as sa

from app.db import (
    assignment_events, assignment_rules, documents, engine,
    households, people, record_assignments, tasks, team_memberships,
    work_approvals, work_assignment_details, work_queues, workflow_instances,
    workflow_steps, timeline_events,
)
from app.security.audit import write_audit_event
from app.security.authorization import (
    CLIENT_ENTITY_TYPES, assignment_manageable, record_in_scope,
)
from app.services.timeline import add_timeline_event
from app.services.work_intelligence import bottlenecks, capacity_metrics, daily_agenda, queue_items

ENTITY_TYPES = {"person", "household", "task", "document", "workflow_instance", "workflow_step", "tax_engagement", "tax_return", "investment_account"}
ASSIGNMENT_ROLES = {"primary", "secondary", "supervisor", "owner"}


def authorize_assignment_target(principal, entity_type, entity_id):
    """Raise PermissionError unless the principal may create an assignment here.

    Assigning a user to a client record (person/household) grants that user
    access to the record, so it requires the dedicated ``assignment.manage``
    capability *and* write scope over the record — it is deliberately separated
    from ordinary ``work.write`` mutation (H1). For work on a specific entity
    (task/document/workflow/tax/account) the principal must have write scope
    over the underlying client record so assignments cannot reach other
    advisors' clients (H8). This helper enforces authorization at the route
    boundary; the underlying ``assign_work`` service remains a trusted internal
    API used by automatic rules and engagement creation.
    """
    if entity_type in CLIENT_ENTITY_TYPES:
        if not principal.can("assignment.manage"):
            raise PermissionError("assignment.manage capability is required to assign client records")
        if not record_in_scope(principal, entity_type, entity_id, write=True):
            raise PermissionError("Record is outside your authorized scope")
        return
    if principal.can("record.write_all"):
        return
    with engine.connect() as connection:
        person_id, household_id = _timeline_target(connection, entity_type, entity_id)
        allowed = (
            record_in_scope(principal, "person", person_id, write=True, connection=connection)
            or record_in_scope(principal, "household", household_id, write=True, connection=connection)
        )
    if not allowed:
        raise PermissionError("Underlying record is outside your authorized scope")


def authorize_existing_assignment(principal, assignment_id):
    """Return the assignment row if the principal may manage it, ``None`` if it
    does not exist, or raise PermissionError if it is out of scope (H8)."""
    with engine.connect() as connection:
        row = connection.execute(
            select(record_assignments).where(record_assignments.c.id == assignment_id)
        ).mappings().one_or_none()
        if row is None:
            return None
        if not assignment_manageable(connection, principal, row):
            raise PermissionError("Assignment is outside your authorized scope")
    return row


def _active(table):
    today = date.today()
    return and_(table.c.effective_date <= today, or_(table.c.inactive_date.is_(None), table.c.inactive_date >= today))


def _timeline_target(connection, entity_type, entity_id):
    if entity_type == "person": return entity_id, None
    if entity_type == "household": return None, entity_id
    if entity_type in {"task", "document"}:
        table = tasks if entity_type == "task" else documents
        row = connection.execute(select(table.c.person_id).where(table.c.id == entity_id)).first()
        return (row.person_id, None) if row else (None, None)
    if entity_type == "workflow_instance":
        row = connection.execute(select(workflow_instances.c.person_id, workflow_instances.c.household_id).where(workflow_instances.c.id == entity_id)).first()
        return (row.person_id, row.household_id) if row else (None, None)
    if entity_type == "workflow_step":
        row = connection.execute(select(workflow_instances.c.person_id, workflow_instances.c.household_id).select_from(workflow_steps.join(workflow_instances)).where(workflow_steps.c.id == entity_id)).first()
        return (row.person_id, row.household_id) if row else (None, None)
    if entity_type in {"tax_engagement", "tax_return"}:
        from app.db import tax_engagement_returns, tax_engagements
        if entity_type == "tax_engagement":
            row = connection.execute(select(tax_engagements.c.person_id, tax_engagements.c.household_id).where(tax_engagements.c.id == entity_id)).first()
        else:
            row = connection.execute(select(tax_engagements.c.person_id, tax_engagements.c.household_id).select_from(tax_engagement_returns.join(tax_engagements)).where(tax_engagement_returns.c.id == entity_id)).first()
        return (row.person_id, row.household_id) if row else (None, None)
    return None, None


def _publish(entity_type, entity_id, event_type, title, assignment_id, metadata):
    with engine.connect() as connection:
        person_id, household_id = _timeline_target(connection, entity_type, entity_id)
    if person_id or household_id:
        add_timeline_event(source="work_management", event_type=event_type, title=title,
            person_id=person_id, household_id=household_id,
            external_id=f"assignment-{assignment_id}-{event_type}-{uuid.uuid4().hex}", event_metadata=metadata)


def assign_work(*, entity_type, entity_id, assignment_role, actor_user_id,
                user_id=None, team_id=None, reason=None, assignment_rule_id=None,
                request_id=None):
    if entity_type not in ENTITY_TYPES: raise ValueError("Unsupported entity type")
    if assignment_role not in ASSIGNMENT_ROLES: raise ValueError("Unsupported assignment role")
    if user_id is None and team_id is None: raise ValueError("A user or team is required")
    if assignment_role == "primary":
        with engine.connect() as connection:
            existing = connection.scalar(select(record_assignments.c.id).where(
                record_assignments.c.entity_type == entity_type, record_assignments.c.entity_id == entity_id,
                record_assignments.c.assignment_type == assignment_role, _active(record_assignments)).limit(1))
        if existing: raise ValueError("An active primary assignment already exists")
    with engine.begin() as connection:
        assignment_id = connection.execute(record_assignments.insert().values(
            user_id=user_id, team_id=team_id, entity_type=entity_type,
            entity_id=entity_id, assignment_type=assignment_role,
        ).returning(record_assignments.c.id)).scalar_one()
        connection.execute(work_assignment_details.insert().values(
            assignment_id=assignment_id, assignment_rule_id=assignment_rule_id,
            assigned_by_user_id=actor_user_id, reason=reason))
        connection.execute(assignment_events.insert().values(
            assignment_id=assignment_id, entity_type=entity_type, entity_id=entity_id,
            event_type="assignment_created", to_user_id=user_id, to_team_id=team_id,
            assignment_role=assignment_role, reason=reason, actor_user_id=actor_user_id))
    metadata = {"user_id": user_id, "team_id": team_id, "role": assignment_role, "reason": reason}
    _publish(entity_type, entity_id, "assignment_created", f"{assignment_role.title()} assignment added", assignment_id, metadata)
    write_audit_event(action="assignment.created", entity_type=entity_type, entity_id=entity_id,
        actor_user_id=actor_user_id, request_id=request_id or f"service-{uuid.uuid4()}", metadata=metadata)
    return assignment_id


def reassign_work(assignment_id, *, actor_user_id, user_id=None, team_id=None, reason=None, request_id=None):
    if user_id is None and team_id is None: raise ValueError("A user or team is required")
    today = date.today(); now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        old = connection.execute(select(record_assignments).where(record_assignments.c.id == assignment_id)).mappings().one_or_none()
        if not old: raise ValueError("Assignment not found")
        connection.execute(record_assignments.update().where(record_assignments.c.id == assignment_id).values(inactive_date=today))
        connection.execute(work_assignment_details.update().where(work_assignment_details.c.assignment_id == assignment_id).values(ended_at=now))
        new_id = connection.execute(record_assignments.insert().values(
            user_id=user_id, team_id=team_id, entity_type=old["entity_type"], entity_id=old["entity_id"],
            assignment_type=old["assignment_type"], effective_date=today,
        ).returning(record_assignments.c.id)).scalar_one()
        connection.execute(work_assignment_details.insert().values(assignment_id=new_id, assigned_by_user_id=actor_user_id, reason=reason))
        connection.execute(assignment_events.insert().values(
            assignment_id=new_id, entity_type=old["entity_type"], entity_id=old["entity_id"], event_type="assignment_changed",
            from_user_id=old["user_id"], to_user_id=user_id, from_team_id=old["team_id"], to_team_id=team_id,
            assignment_role=old["assignment_type"], reason=reason, actor_user_id=actor_user_id))
    metadata = {"from_user_id": old["user_id"], "to_user_id": user_id, "from_team_id": old["team_id"], "to_team_id": team_id, "reason": reason}
    _publish(old["entity_type"], old["entity_id"], "assignment_changed", "Assignment changed", new_id, metadata)
    write_audit_event(action="assignment.reassigned", entity_type=old["entity_type"], entity_id=old["entity_id"], actor_user_id=actor_user_id, request_id=request_id or f"service-{uuid.uuid4()}", metadata=metadata)
    return new_id


def deactivate_assignment(assignment_id, *, actor_user_id, reason=None, request_id=None):
    today = date.today(); now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        old = connection.execute(select(record_assignments).where(record_assignments.c.id == assignment_id)).mappings().one_or_none()
        if not old: raise ValueError("Assignment not found")
        connection.execute(record_assignments.update().where(record_assignments.c.id == assignment_id).values(inactive_date=today))
        connection.execute(work_assignment_details.update().where(work_assignment_details.c.assignment_id == assignment_id).values(ended_at=now))
        connection.execute(assignment_events.insert().values(assignment_id=assignment_id, entity_type=old["entity_type"], entity_id=old["entity_id"], event_type="assignment_removed", from_user_id=old["user_id"], from_team_id=old["team_id"], assignment_role=old["assignment_type"], reason=reason, actor_user_id=actor_user_id))
    _publish(old["entity_type"], old["entity_id"], "assignment_removed", "Assignment removed", assignment_id, {"reason": reason})
    write_audit_event(action="assignment.removed", entity_type=old["entity_type"], entity_id=old["entity_id"], actor_user_id=actor_user_id, request_id=request_id or f"service-{uuid.uuid4()}", metadata={"reason": reason})


def apply_assignment_rules(entity_type, entity_id, attributes, actor_user_id, request_id=None):
    with engine.connect() as connection:
        rules = connection.execute(select(assignment_rules).where(assignment_rules.c.active.is_(True), assignment_rules.c.entity_type == entity_type).order_by(assignment_rules.c.priority, assignment_rules.c.id)).mappings().all()
    created = []
    for rule in rules:
        if all(attributes.get(key) == value for key, value in (rule["conditions"] or {}).items()):
            created.append(assign_work(entity_type=entity_type, entity_id=entity_id, assignment_role=rule["assignment_role"], user_id=rule["assignee_user_id"], team_id=rule["assignee_team_id"], actor_user_id=actor_user_id, assignment_rule_id=rule["id"], reason=f"Automatic rule: {rule['name']}", request_id=request_id))
            if rule["assignment_role"] == "primary": break
    return created


def _team_ids(connection, principal):
    return list(connection.scalars(select(team_memberships.c.team_id).where(team_memberships.c.user_id == principal.user_id, _active(team_memberships))))


def authorized_assignments(connection, principal):
    query = select(record_assignments).where(_active(record_assignments))
    if not principal.can("record.read_all"):
        teams = _team_ids(connection, principal)
        query = query.where(or_(record_assignments.c.user_id == principal.user_id, record_assignments.c.team_id.in_(teams) if teams else sa.false()))
    return connection.execute(query).mappings().all()


def work_items(principal, filters=None):
    filters = filters or {}
    with engine.connect() as connection:
        assignments = authorized_assignments(connection, principal)
        assigned = {(row["entity_type"], row["entity_id"]) for row in assignments}
        direct_all = principal.can("record.read_all")
        # Push the authorization scope into SQL so the read is O(caller's book)
        # rather than O(all tasks + all workflow steps) (RC8/RC9 H15-H19). The
        # scoped rows are exactly those the prior Python-side filter kept.
        person_ids = {eid for (etype, eid) in assigned if etype == "person"}
        household_ids = {eid for (etype, eid) in assigned if etype == "household"}
        task_ids = {eid for (etype, eid) in assigned if etype == "task"}
        step_ids = {eid for (etype, eid) in assigned if etype == "workflow_step"}
        workflow_ids = {eid for (etype, eid) in assigned if etype == "workflow_instance"}
        task_query = select(tasks)
        step_query = select(workflow_steps, workflow_instances.c.id.label("parent_workflow_id"), workflow_instances.c.name.label("workflow_name"), workflow_instances.c.person_id, workflow_instances.c.household_id).join(workflow_instances)
        if not direct_all:
            task_scope = [c for c in (
                tasks.c.id.in_(task_ids) if task_ids else None,
                tasks.c.person_id.in_(person_ids) if person_ids else None,
                tasks.c.household_id.in_(household_ids) if household_ids else None,
            ) if c is not None]
            task_query = task_query.where(or_(*task_scope) if task_scope else sa.false())
            step_scope = [c for c in (
                workflow_steps.c.id.in_(step_ids) if step_ids else None,
                workflow_instances.c.id.in_(workflow_ids) if workflow_ids else None,
                workflow_instances.c.person_id.in_(person_ids) if person_ids else None,
                workflow_instances.c.household_id.in_(household_ids) if household_ids else None,
            ) if c is not None]
            step_query = step_query.where(or_(*step_scope) if step_scope else sa.false())
        task_rows = connection.execute(task_query).mappings().all()
        step_rows = connection.execute(step_query).mappings().all()
    items = []
    for row in task_rows:
        item = dict(row); item.update({"entity_type": "task", "entity_id": row["id"], "assigned": ("task", row["id"]) in assigned, "title": row["title"]}); items.append(item)
    for row in step_rows:
        item = dict(row); item.update({"entity_type": "workflow_step", "entity_id": row["id"], "assigned": ("workflow_step", row["id"]) in assigned, "title": row["name"], "work_type": "workflow"}); items.append(item)
    for key in ("priority", "status", "team_id"):
        if filters.get(key) not in (None, ""): items = [item for item in items if str(item.get(key) or "") == str(filters[key])]
    if filters.get("due_before"): items = [item for item in items if item.get("due_date") and item["due_date"] <= filters["due_before"]]
    if filters.get("workflow"): items = [item for item in items if filters["workflow"].lower() in str(item.get("workflow_name") or "").lower()]
    if filters.get("assignee"):
        wanted = int(filters["assignee"])
        allowed = {(row["entity_type"], row["entity_id"]) for row in assignments if row["user_id"] == wanted}
        items = [item for item in items if (item["entity_type"], item["entity_id"]) in allowed]
    return items


def dashboard(principal, filters=None):
    filters = filters or {}
    items = work_items(principal, filters)
    with engine.connect() as connection:
        queues = connection.execute(select(work_queues).where(work_queues.c.active.is_(True)).order_by(work_queues.c.name)).mappings().all()
        assignments = authorized_assignments(connection, principal)
        approvals = connection.execute(select(work_approvals).where(work_approvals.c.status == "pending", or_(work_approvals.c.approver_user_id == principal.user_id, work_approvals.c.approver_team_id.in_(_team_ids(connection, principal))))).mappings().all()
        assigned_people = {row["entity_id"] for row in assignments if row["entity_type"] == "person"}
        assigned_households = {row["entity_id"] for row in assignments if row["entity_type"] == "household"}
        people_rows = connection.execute(select(people).where(people.c.id.in_(assigned_people))).mappings().all() if assigned_people else []
        household_rows = connection.execute(select(households).where(households.c.id.in_(assigned_households))).mappings().all() if assigned_households else []
        scoped_people = assigned_people if not principal.can("record.read_all") else set(connection.scalars(select(people.c.id)))
        review_documents = connection.execute(select(documents).where(documents.c.person_id.in_(scoped_people), documents.c.review_status.in_(("pending", "ready_for_review")))).mappings().all() if scoped_people else []
        meetings = connection.execute(select(timeline_events).where(timeline_events.c.person_id.in_(scoped_people), timeline_events.c.event_type == "calendar_event", timeline_events.c.event_time >= datetime.now(timezone.utc)).order_by(timeline_events.c.event_time).limit(50)).mappings().all() if scoped_people else []
    agenda = daily_agenda(items)
    if filters.get("queue"):
        selected = next((queue for queue in queues if queue["code"] == filters["queue"]), None)
        agenda = daily_agenda(queue_items(items, selected["criteria"] or {})) if selected else []
    queue_summary = [{"code": queue["code"], "name": queue["name"], "count": len(queue_items(items, queue["criteria"] or {}))} for queue in queues]
    today = date.today()
    overdue = [item for item in agenda if item.get("due_date") and item["due_date"] < today and item.get("status") not in {"complete", "completed", "closed"}]
    sla_risks = [item for item in agenda if item["sla_risk"]["level"] in {"warning", "critical", "breached"}]
    domain_panels = {
        "advisor": {"assigned_households": len(household_rows), "upcoming_meetings": len(meetings), "waiting_on_client": sum(item.get("waiting_on") == "client" for item in agenda), "reviews_due": len(review_documents)},
        "operations": {key: sum(item.get("work_type") == key for item in agenda) for key in ("account_opening", "transfer", "paperwork", "pending_custodian", "missing_document")},
        "tax": {key: sum(item.get("work_type") == key for item in agenda) for key in ("return_received", "tax_preparation", "tax_review", "extension", "irs_notice")},
        "management": {"workload": len(agenda), "sla_violations": sum(item["sla_risk"]["level"] == "breached" for item in agenda), "revenue_pipeline": None},
    }
    return {"items": agenda, "assigned_people": people_rows, "assigned_households": household_rows,
            "documents_awaiting_review": review_documents, "upcoming_meetings": meetings,
            "overdue_items": overdue, "sla_risks": sla_risks, "approvals": approvals,
            "queues": queue_summary, "capacity": capacity_metrics(items),
            "bottlenecks": bottlenecks(items), "domain_panels": domain_panels,
            "placeholders": {"revenue_pipeline": "Unavailable until a revenue domain is implemented."}}


def list_assignments(principal):
    with engine.connect() as connection:
        return authorized_assignments(connection, principal)


def queue_detail(principal, code, filters=None):
    with engine.connect() as connection:
        queue = connection.execute(select(work_queues).where(work_queues.c.code == code, work_queues.c.active.is_(True))).mappings().one_or_none()
    if not queue: raise ValueError("Queue not found")
    return {"queue": queue, "items": daily_agenda(queue_items(work_items(principal, filters), queue["criteria"] or {}))}
