"""Operational tasks, dependencies, checklists, issues & comments (Phase D.20).

An operational task is firm work — it may belong to a project/phase or stand alone, and any client
link is optional. Dependencies are deterministic (finish-to-start gating on activation; cycles are
rejected). Checklists, issues (risk/issue), and comments hang off tasks/projects. Advisor Work
remains the authoritative client-work domain; a task may only REFERENCE an advisor-work item.
Approved ``task_completed`` events publish to the timeline when the task carries a client anchor.
"""
from __future__ import annotations

from sqlalchemy import and_, func, select

from app.database.operations_tables import (
    ISSUE_STATUSES,
    ISSUE_TYPES,
    OPERATIONAL_STATUSES,
    PRIORITIES,
    SEVERITIES,
)
from app.db import engine
from app.db import operational_checklist_items as checklist_t
from app.db import operational_comments as comments_t
from app.db import operational_issues as issues_t
from app.db import operational_task_dependencies as deps_t
from app.db import operational_tasks as tasks_t
from app.db import projects as projects_t

from .common import (
    OperationsError,
    OperationsNotFound,
    can_write,
    now,
    publish_timeline,
    record_event,
    require_anchor_write,
    scope_clause,
    visible,
)

_TRANSITIONS = {
    "planned": {"active", "on_hold", "cancelled", "archived"},
    "active": {"blocked", "on_hold", "completed", "cancelled"},
    "blocked": {"active", "on_hold", "cancelled"},
    "on_hold": {"active", "cancelled", "archived"},
    "completed": {"archived"},
    "cancelled": {"archived"},
    "archived": set(),
}
_CLOSED = ("completed", "cancelled", "archived")


def _load_scoped(c, principal, task_id: int, *, write=False) -> dict:
    t = c.execute(select(tasks_t).where(tasks_t.c.id == task_id)).mappings().first()
    if t is None or not visible(principal, dict(t), c):
        raise OperationsNotFound(str(task_id))
    t = dict(t)
    if write and not can_write(principal, t, c):
        raise OperationsError("write not permitted in record scope")
    return t


# --- reads -------------------------------------------------------------------

def list_tasks(principal, *, status=None, project_id=None, assigned_user_id=None, search=None,
               open_only=False, page=1, page_size=50) -> dict:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    with engine.connect() as c:
        scope = scope_clause(tasks_t, principal, c)
        conds = []
        if scope is not None:
            conds.append(scope)
        if status:
            conds.append(tasks_t.c.status == status)
        if project_id:
            conds.append(tasks_t.c.project_id == project_id)
        if assigned_user_id:
            conds.append(tasks_t.c.assigned_user_id == assigned_user_id)
        if search:
            conds.append(tasks_t.c.title.ilike(f"%{search.strip()}%"))
        if open_only:
            conds.append(tasks_t.c.status.notin_(_CLOSED))
        where = and_(*conds) if conds else None
        base = select(func.count()).select_from(tasks_t)
        total = c.scalar(base.where(where) if where is not None else base)
        stmt = select(tasks_t)
        if where is not None:
            stmt = stmt.where(where)
        rows = [dict(r) for r in c.execute(
            stmt.order_by(tasks_t.c.id.desc()).limit(page_size)
            .offset((page - 1) * page_size)).mappings()]
    return {"rows": rows, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size if total else 0}


def get_task(principal, task_id: int) -> dict | None:
    with engine.connect() as c:
        try:
            t = _load_scoped(c, principal, task_id)
        except (OperationsNotFound, OperationsError):
            return None
        t["dependencies"] = [dict(r) for r in c.execute(
            select(deps_t).where(deps_t.c.task_id == task_id)).mappings()]
        t["checklist"] = [dict(r) for r in c.execute(
            select(checklist_t).where(checklist_t.c.task_id == task_id)
            .order_by(checklist_t.c.position, checklist_t.c.id)).mappings()]
        t["issues"] = [dict(r) for r in c.execute(
            select(issues_t).where(issues_t.c.task_id == task_id).order_by(issues_t.c.id)).mappings()]
        t["comments"] = [dict(r) for r in c.execute(
            select(comments_t).where(comments_t.c.task_id == task_id)
            .order_by(comments_t.c.id)).mappings()]
    return t


# --- tasks -------------------------------------------------------------------

def create_task(principal, *, title, project_id=None, phase_id=None, milestone_id=None,
                description=None, priority="normal", department=None, estimated_minutes=None,
                due_date=None, assigned_user_id=None, assigned_resource_id=None, person_id=None,
                household_id=None, organization_id=None, advisor_work_item_id=None, meeting_id=None,
                conversation_id=None, document_id=None, workflow_instance_id=None,
                actor_user_id=None) -> dict:
    title = (title or "").strip()
    if not title:
        raise OperationsError("title is required")
    if priority not in PRIORITIES:
        raise OperationsError(f"invalid priority {priority!r}")
    require_anchor_write(principal, person_id=person_id, household_id=household_id,
                         organization_id=organization_id)
    ts = now()
    with engine.begin() as c:
        if project_id is not None and c.scalar(
                select(projects_t.c.id).where(projects_t.c.id == project_id)) is None:
            raise OperationsError("project not found")
        t = c.execute(tasks_t.insert().values(
            title=title, project_id=project_id, phase_id=phase_id, milestone_id=milestone_id,
            description=description, status="planned", priority=priority, department=department,
            estimated_minutes=estimated_minutes, due_date=due_date, assigned_user_id=assigned_user_id,
            assigned_resource_id=assigned_resource_id, person_id=person_id, household_id=household_id,
            organization_id=organization_id, advisor_work_item_id=advisor_work_item_id,
            meeting_id=meeting_id, conversation_id=conversation_id, document_id=document_id,
            workflow_instance_id=workflow_instance_id, last_status_at=ts,
            created_by_user_id=actor_user_id, created_at=ts, updated_at=ts)
            .returning(*tasks_t.c)).mappings().one()
        t = dict(t)
        record_event(c, entity_type="task", entity_id=t["id"], project_id=project_id,
                     event_type="task_created", to_status="planned", actor_user_id=actor_user_id,
                     payload={"title": title})
        # (D.35) Publish the completed business FACT to the domain-event model (references only), in the
        # caller's transaction, best-effort so it can never corrupt the mutation. Additive; consumers are
        # dark-launched, so behavior is unchanged.
        from app.services.events import publisher
        publisher.publish_safe("operations.task_created",
                               {"task_id": t["id"], "project_id": project_id, "status": "planned",
                                "priority": priority}, conn=c, producer="operations.tasks",
                               subject_ref=f"operations_task:{t['id']}")
        return t


def update_task(principal, task_id: int, *, actor_user_id=None, **fields) -> dict:
    allowed = {"title", "description", "priority", "department", "estimated_minutes",
               "actual_minutes", "due_date", "phase_id", "milestone_id", "tags", "task_metadata",
               "advisor_work_item_id", "meeting_id", "conversation_id", "document_id",
               "workflow_instance_id"}
    values = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not values:
        raise OperationsError("no updatable fields provided")
    with engine.begin() as c:
        _load_scoped(c, principal, task_id, write=True)
        values["updated_at"] = now()
        t = c.execute(tasks_t.update().where(tasks_t.c.id == task_id)
                      .values(**values).returning(*tasks_t.c)).mappings().one()
        record_event(c, entity_type="task", entity_id=task_id, event_type="task_updated",
                     actor_user_id=actor_user_id, payload={"fields": sorted(values.keys())})
        return dict(t)


def assign_task(principal, task_id: int, *, assigned_user_id=None, assigned_resource_id=None,
                actor_user_id=None) -> dict:
    with engine.begin() as c:
        _load_scoped(c, principal, task_id, write=True)
        values = {"updated_at": now()}
        if assigned_user_id is not None:
            values["assigned_user_id"] = assigned_user_id
        if assigned_resource_id is not None:
            values["assigned_resource_id"] = assigned_resource_id
        t = c.execute(tasks_t.update().where(tasks_t.c.id == task_id)
                      .values(**values).returning(*tasks_t.c)).mappings().one()
        record_event(c, entity_type="task", entity_id=task_id, event_type="task_assigned",
                     actor_user_id=actor_user_id,
                     payload={"assigned_user_id": assigned_user_id,
                              "assigned_resource_id": assigned_resource_id})
        return dict(t)


def _incomplete_dependencies(c, task_id: int) -> list[int]:
    dep_ids = list(c.scalars(select(deps_t.c.depends_on_task_id).where(
        deps_t.c.task_id == task_id, deps_t.c.dependency_type == "finish_to_start")))
    if not dep_ids:
        return []
    return list(c.scalars(select(tasks_t.c.id).where(
        tasks_t.c.id.in_(tuple(dep_ids)), tasks_t.c.status != "completed")))


def transition_task(principal, task_id: int, status: str, *, actor_user_id=None, reason=None) -> dict:
    if status not in OPERATIONAL_STATUSES:
        raise OperationsError(f"invalid status {status!r}")
    with engine.begin() as c:
        t = _load_scoped(c, principal, task_id, write=True)
        current = t["status"]
        if status != current and status not in _TRANSITIONS.get(current, set()):
            raise OperationsError(f"cannot transition {current!r} -> {status!r}")
        # Deterministic finish-to-start dependency gate on activation.
        if status == "active" and _incomplete_dependencies(c, task_id):
            raise OperationsError("blocked by incomplete finish-to-start dependencies")
        ts = now()
        values = {"status": status, "last_status_at": ts, "updated_at": ts}
        if status == "active" and t["started_at"] is None:
            values["started_at"] = ts
        if status == "completed":
            values["completed_at"] = ts
        updated = c.execute(tasks_t.update().where(tasks_t.c.id == task_id)
                            .values(**values).returning(*tasks_t.c)).mappings().one()
        record_event(c, entity_type="task", entity_id=task_id, project_id=t.get("project_id"),
                     event_type=f"task_{status}", from_status=current, to_status=status,
                     actor_user_id=actor_user_id, payload={"reason": reason})
        updated = dict(updated)
        # (D.35) Publish the completion fact only on a genuine transition to completed.
        if status == "completed" and current != "completed":
            from app.services.events import publisher
            publisher.publish_safe("operations.task_completed",
                                   {"task_id": task_id, "from_status": current, "to_status": status},
                                   conn=c, producer="operations.tasks",
                                   subject_ref=f"operations_task:{task_id}")
    if status == "completed":
        publish_timeline(updated, "task_completed")
    return updated


# --- dependencies ------------------------------------------------------------

def _would_cycle(c, task_id: int, depends_on_task_id: int) -> bool:
    """True if depends_on_task_id already (transitively) depends on task_id."""
    seen, frontier = set(), [depends_on_task_id]
    while frontier:
        cur = frontier.pop()
        if cur == task_id:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        frontier.extend(c.scalars(select(deps_t.c.depends_on_task_id).where(deps_t.c.task_id == cur)))
    return False


def add_dependency(principal, task_id: int, depends_on_task_id: int, *,
                   dependency_type="finish_to_start", actor_user_id=None) -> dict:
    if task_id == depends_on_task_id:
        raise OperationsError("a task cannot depend on itself")
    with engine.begin() as c:
        t = _load_scoped(c, principal, task_id, write=True)
        if c.scalar(select(tasks_t.c.id).where(tasks_t.c.id == depends_on_task_id)) is None:
            raise OperationsError("dependency target not found")
        if _would_cycle(c, task_id, depends_on_task_id):
            raise OperationsError("dependency would create a cycle")
        row = c.execute(deps_t.insert().values(
            task_id=task_id, depends_on_task_id=depends_on_task_id,
            dependency_type=dependency_type).returning(*deps_t.c)).mappings().one()
        record_event(c, entity_type="task", entity_id=task_id, project_id=t.get("project_id"),
                     event_type="dependency_added", actor_user_id=actor_user_id,
                     payload={"depends_on": depends_on_task_id})
        return dict(row)


# --- checklist ---------------------------------------------------------------

def add_checklist_item(principal, task_id: int, *, description, position=0, actor_user_id=None) -> dict:
    description = (description or "").strip()
    if not description:
        raise OperationsError("checklist item description is required")
    with engine.begin() as c:
        _load_scoped(c, principal, task_id, write=True)
        row = c.execute(checklist_t.insert().values(
            task_id=task_id, description=description, position=int(position))
            .returning(*checklist_t.c)).mappings().one()
        return dict(row)


def toggle_checklist_item(principal, item_id: int, *, done=True, actor_user_id=None) -> dict:
    with engine.begin() as c:
        item = c.execute(select(checklist_t).where(checklist_t.c.id == item_id)).mappings().first()
        if item is None:
            raise OperationsNotFound(f"checklist item {item_id}")
        _load_scoped(c, principal, item["task_id"], write=True)
        values = {"done": bool(done), "done_at": (now() if done else None),
                  "done_by_user_id": (actor_user_id if done else None)}
        row = c.execute(checklist_t.update().where(checklist_t.c.id == item_id)
                        .values(**values).returning(*checklist_t.c)).mappings().one()
        return dict(row)


# --- issues / risks ----------------------------------------------------------

def add_issue(principal, *, title, issue_type="issue", project_id=None, task_id=None,
              severity="medium", description=None, owner_user_id=None, due_date=None,
              actor_user_id=None) -> dict:
    title = (title or "").strip()
    if not title:
        raise OperationsError("issue title is required")
    if issue_type not in ISSUE_TYPES:
        raise OperationsError(f"invalid issue_type {issue_type!r}")
    if severity not in SEVERITIES:
        raise OperationsError(f"invalid severity {severity!r}")
    if project_id is None and task_id is None:
        raise OperationsError("an issue must reference a project or task")
    with engine.begin() as c:
        if task_id is not None:
            _load_scoped(c, principal, task_id, write=True)
        elif c.scalar(select(projects_t.c.id).where(projects_t.c.id == project_id)) is None:
            raise OperationsError("project not found")
        row = c.execute(issues_t.insert().values(
            project_id=project_id, task_id=task_id, issue_type=issue_type, title=title,
            severity=severity, description=description, owner_user_id=owner_user_id,
            due_date=due_date, status="open", created_by_user_id=actor_user_id)
            .returning(*issues_t.c)).mappings().one()
        record_event(c, entity_type="issue", entity_id=dict(row)["id"], project_id=project_id,
                     event_type=f"{issue_type}_opened", actor_user_id=actor_user_id,
                     payload={"severity": severity})
        return dict(row)


def set_issue_status(principal, issue_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in ISSUE_STATUSES:
        raise OperationsError(f"invalid issue status {status!r}")
    with engine.begin() as c:
        issue = c.execute(select(issues_t).where(issues_t.c.id == issue_id)).mappings().first()
        if issue is None:
            raise OperationsNotFound(f"issue {issue_id}")
        if issue["task_id"] is not None:
            _load_scoped(c, principal, issue["task_id"], write=True)
        ts = now()
        values = {"status": status, "updated_at": ts}
        if status in ("resolved", "closed"):
            values["resolved_at"] = ts
        row = c.execute(issues_t.update().where(issues_t.c.id == issue_id)
                        .values(**values).returning(*issues_t.c)).mappings().one()
        record_event(c, entity_type="issue", entity_id=issue_id, project_id=issue["project_id"],
                     event_type=f"issue_{status}", to_status=status, actor_user_id=actor_user_id)
        return dict(row)


# --- comments ----------------------------------------------------------------

def add_comment(principal, *, body, project_id=None, task_id=None, actor_user_id=None) -> dict:
    body = (body or "").strip()
    if not body:
        raise OperationsError("comment body is required")
    if project_id is None and task_id is None:
        raise OperationsError("a comment must reference a project or task")
    with engine.begin() as c:
        if task_id is not None:
            _load_scoped(c, principal, task_id, write=True)
        row = c.execute(comments_t.insert().values(
            project_id=project_id, task_id=task_id, body=body,
            author_user_id=actor_user_id).returning(*comments_t.c)).mappings().one()
        return dict(row)


# --- metrics -----------------------------------------------------------------

def task_metrics(principal) -> dict:
    with engine.connect() as c:
        scope = scope_clause(tasks_t, principal, c)
        def _count(*extra):
            stmt = select(func.count()).select_from(tasks_t)
            conds = [] if scope is None else [scope]
            conds.extend(extra)
            return c.scalar(stmt.where(and_(*conds)) if conds else stmt) or 0
        return {"total": _count(), "open": _count(tasks_t.c.status.notin_(_CLOSED)),
                "active": _count(tasks_t.c.status == "active"),
                "completed": _count(tasks_t.c.status == "completed")}
