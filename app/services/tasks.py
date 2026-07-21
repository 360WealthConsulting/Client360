"""Person tasks — thin service over the existing ``tasks`` table (Sprint 1, Task 4).

Centralises person-task creation/completion so both the Tasks tab and the Notes page (activity
note -> optional follow-up task) share one path. It reuses the existing ``tasks`` table,
statuses, priorities, and ``assigned_to`` field — it does **not** introduce a parallel task
system, notifications, recurring tasks, workflow automation, or dashboards.

Each write records a client **timeline** event and an **audit** event; creation is guarded
against rapid form-resubmission by an idempotency window on (person, title).
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.db import engine, people, record_assignments, tasks, user_roles, users
from app.security.audit import write_audit_event
from app.services.timeline import add_timeline_event

#: A just-created identical open task within this window is treated as a duplicate submission.
_DEDUPE_WINDOW = timedelta(seconds=20)


def _person_exists(c, person_id: int) -> bool:
    return c.execute(select(people.c.id).where(people.c.id == person_id)).scalar_one_or_none() is not None


def assignable_users(conn=None):
    """Provisioned staff available as task assignees: active users holding at least one active
    role. Scoping to role-holders (rather than *every* active user) keeps the picker to real
    staff and excludes system/orphan user rows — the canonical user model, never free text."""
    def _do(c):
        return c.execute(
            select(users.c.id, users.c.display_name)
            .select_from(users.join(user_roles, and_(
                user_roles.c.user_id == users.c.id,
                user_roles.c.inactive_date.is_(None),
            )))
            .where(users.c.status == "active")
            .distinct()
            .order_by(users.c.display_name)
        ).mappings().all()

    if conn is not None:
        return _do(conn)
    with engine.connect() as c:
        return _do(c)


def _active_user_exists(c, user_id: int) -> bool:
    return c.execute(select(users.c.id).where(
        users.c.id == user_id, users.c.status == "active")).scalar_one_or_none() is not None


def tasks_with_assignee(person_id: int, *, conn=None):
    """A person's tasks with the current **primary** assignee resolved from the canonical
    ``record_assignments`` model (LEFT JOIN, so legacy/unassigned tasks are preserved with a
    NULL ``assignee_name``). This is the single assignee-resolution used by every client view —
    the dedicated Tasks page and the Client Profile Tasks tab — so both render identically.
    Callers display ``assignee_name or assigned_to or "Unassigned"`` to keep the legacy free-text
    fallback for historical rows that predate canonical assignment."""
    def _do(c):
        return c.execute(
            select(tasks, users.c.display_name.label("assignee_name"),
                   record_assignments.c.user_id.label("assignee_user_id"))
            .select_from(
                tasks
                .outerjoin(record_assignments, and_(
                    record_assignments.c.entity_type == "task",
                    record_assignments.c.entity_id == tasks.c.id,
                    record_assignments.c.assignment_type == "primary",
                    record_assignments.c.inactive_date.is_(None),
                ))
                .outerjoin(users, users.c.id == record_assignments.c.user_id)
            )
            .where(tasks.c.person_id == person_id)
            .order_by(tasks.c.status, tasks.c.due_date.asc().nullslast(), tasks.c.created_at.desc())
        ).mappings().all()

    if conn is not None:
        return _do(conn)
    with engine.connect() as c:
        return _do(c)


#: Task statuses that are not open work (mirrors the Advisor Workspace dashboard).
_CLOSED_TASK_STATUSES = frozenset({"complete", "completed", "closed", "cancelled", "resolved"})


def open_tasks_for_people(person_ids, *, limit=200):
    """Read-only list of **open** tasks across a **set** of person ids — the
    authoritative book-scoped read behind the Advisor Intelligence "overdue open
    task" signal (Phase D.5B). Open = status not in the closed set (the same
    task-status vocabulary the dashboard uses; this does not recompute status, it
    filters on the stored field). ``person_ids`` scopes the read: ``None`` =
    unrestricted (record.read_all), an empty collection = ``[]``, otherwise only
    tasks keyed to one of those person ids. The caller derives "overdue" from the
    returned ``due_date`` — this read stays a plain scoped read. Returns id, title,
    due_date, status, person_id, household_id."""
    if person_ids is not None and len(person_ids) == 0:
        return []
    stmt = select(
        tasks.c.id, tasks.c.title, tasks.c.due_date, tasks.c.status,
        tasks.c.person_id, tasks.c.household_id,
    ).where(tasks.c.status.notin_(tuple(_CLOSED_TASK_STATUSES)))
    if person_ids is not None:
        stmt = stmt.where(tasks.c.person_id.in_(tuple(person_ids)))
    stmt = stmt.order_by(tasks.c.due_date.asc().nullslast(), tasks.c.id.asc()).limit(limit)
    with engine.connect() as c:
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_task(person_id: int, *, title: str, description: str | None = None,
                priority: str = "normal", assigned_to_user_id: int | None = None,
                due_date: date | None = None, actor_user_id: int | None = None,
                request_id: str | None = None, source: str | None = None,
                source_note_id: int | None = None, idempotency_key: str | None = None,
                conn=None) -> int | None:
    """Create an open task for a person (reusing the ``tasks`` table). The assignee is an
    existing **user** (validated) assigned via the canonical ``record_assignments`` model
    (``work_management.assign_work``) — never free-text. Returns the task id, or ``None`` if
    suppressed as a duplicate resubmission (either the same ``idempotency_key`` was already used,
    or the short-window heuristic fired). Records timeline + audit only for a real insert."""
    title = (title or "").strip()
    if not title:
        raise ValueError("Task title is required.")
    idempotency_key = (idempotency_key or "").strip() or None

    def _do(c):
        if not _person_exists(c, person_id):
            raise ValueError("Person not found.")
        if assigned_to_user_id is not None and not _active_user_exists(c, assigned_to_user_id):
            raise ValueError("Assignee must be an existing active user.")
        # Short-window heuristic guard (covers submissions with no idempotency key).
        recent = c.execute(
            select(tasks.c.id).where(
                tasks.c.person_id == person_id, tasks.c.title == title, tasks.c.status == "open",
                tasks.c.created_at > datetime.now(UTC) - _DEDUPE_WINDOW,
            ).limit(1)
        ).scalar()
        if recent is not None:
            return None
        # Definitive guard: a unique idempotency_key makes a resubmit a no-op (returns None),
        # so a browser back/resubmit or retried POST can never create a second task.
        statement = insert(tasks).values(
            person_id=person_id, title=title, description=(description or None), status="open",
            priority=priority, due_date=due_date, idempotency_key=idempotency_key,
            created_by_user_id=actor_user_id, updated_by_user_id=actor_user_id,
        )
        if idempotency_key is not None:
            statement = statement.on_conflict_do_nothing(index_elements=[tasks.c.idempotency_key])
        return c.execute(statement.returning(tasks.c.id)).scalar_one_or_none()

    task_id = _run(conn, _do)
    if task_id is None:
        return None

    meta = {"task_id": task_id, "priority": priority, "assigned_to_user_id": assigned_to_user_id,
            "due_date": due_date.isoformat() if due_date else None}
    if source_note_id is not None:
        meta["source_note_id"] = source_note_id
    add_timeline_event(person_id=person_id, source="client360", event_type="task_created",
                       title="Task created", summary=title, external_id=f"task-created-{task_id}",
                       event_metadata=meta)
    _audit("task.created", task_id, person_id, actor_user_id, request_id,
           {"source": source, "source_note_id": source_note_id})

    # Reuse the canonical user/assignee model (record_assignments) rather than free text.
    if assigned_to_user_id is not None:
        from app.services.work_management import assign_work
        assign_work(entity_type="task", entity_id=task_id, assignment_role="primary",
                    actor_user_id=actor_user_id, user_id=assigned_to_user_id, request_id=request_id)
    return task_id


def complete_task(person_id: int, task_id: int, *, actor_user_id: int | None = None,
                  request_id: str | None = None, conn=None) -> bool:
    """Mark a person's task complete. Returns False if the task was not found. Records
    timeline + audit; a status change that is a no-op is not re-emitted."""
    def _do(c):
        task = c.execute(
            select(tasks).where(tasks.c.id == task_id, tasks.c.person_id == person_id)
        ).mappings().one_or_none()
        if task is None:
            return None
        if task["status"] == "complete":
            return task  # already complete -> no duplicate timeline/audit
        now = datetime.now(UTC)
        c.execute(update(tasks).where(tasks.c.id == task_id, tasks.c.person_id == person_id).values(
            status="complete", completed_at=now, updated_at=now, updated_by_user_id=actor_user_id))
        return {**dict(task), "_changed_at": now}

    task = _run(conn, _do)
    if task is None:
        return False
    if "_changed_at" not in task:
        return True  # was already complete
    add_timeline_event(person_id=person_id, source="client360", event_type="task_completed",
                       title="Task completed", summary=task["title"], event_time=task["_changed_at"],
                       external_id=f"task-completed-{task_id}",
                       event_metadata={"task_id": task_id, "priority": task["priority"],
                                       "assigned_to": task["assigned_to"]})
    _audit("task.completed", task_id, person_id, actor_user_id, request_id, {"from_status": task["status"]})
    return True


def _audit(action, task_id, person_id, actor_user_id, request_id, metadata):
    import uuid
    write_audit_event(
        action=action, entity_type="task", entity_id=task_id, actor_user_id=actor_user_id,
        request_id=request_id or f"task-{uuid.uuid4()}", metadata={"person_id": person_id, **metadata},
    )


def _run(conn, fn):
    if conn is not None:
        return fn(conn)
    with engine.begin() as c:
        return fn(c)
