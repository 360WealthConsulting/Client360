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

from sqlalchemy import insert, select, update

from app.db import engine, people, tasks, users
from app.security.audit import write_audit_event
from app.services.timeline import add_timeline_event

#: A just-created identical open task within this window is treated as a duplicate submission.
_DEDUPE_WINDOW = timedelta(seconds=20)


def _person_exists(c, person_id: int) -> bool:
    return c.execute(select(people.c.id).where(people.c.id == person_id)).scalar_one_or_none() is not None


def assignable_users(conn=None):
    """Active users available as task assignees (the canonical user model, not free text)."""
    def _do(c):
        return c.execute(
            select(users.c.id, users.c.display_name)
            .where(users.c.status == "active").order_by(users.c.display_name)
        ).mappings().all()

    if conn is not None:
        return _do(conn)
    with engine.connect() as c:
        return _do(c)


def _active_user_exists(c, user_id: int) -> bool:
    return c.execute(select(users.c.id).where(
        users.c.id == user_id, users.c.status == "active")).scalar_one_or_none() is not None


def create_task(person_id: int, *, title: str, description: str | None = None,
                priority: str = "normal", assigned_to_user_id: int | None = None,
                due_date: date | None = None, actor_user_id: int | None = None,
                request_id: str | None = None, source: str | None = None,
                source_note_id: int | None = None, conn=None) -> int | None:
    """Create an open task for a person (reusing the ``tasks`` table). The assignee is an
    existing **user** (validated) assigned via the canonical ``record_assignments`` model
    (``work_management.assign_work``) — never free-text. Returns the task id, or ``None`` if
    suppressed as a duplicate resubmission. Records timeline + audit."""
    title = (title or "").strip()
    if not title:
        raise ValueError("Task title is required.")

    def _do(c):
        if not _person_exists(c, person_id):
            raise ValueError("Person not found.")
        if assigned_to_user_id is not None and not _active_user_exists(c, assigned_to_user_id):
            raise ValueError("Assignee must be an existing active user.")
        # practical duplicate-submission guard: identical open task created moments ago.
        recent = c.execute(
            select(tasks.c.id).where(
                tasks.c.person_id == person_id, tasks.c.title == title, tasks.c.status == "open",
                tasks.c.created_at > datetime.now(UTC) - _DEDUPE_WINDOW,
            ).limit(1)
        ).scalar()
        if recent is not None:
            return None
        return c.execute(
            insert(tasks).values(
                person_id=person_id, title=title, description=(description or None), status="open",
                priority=priority, due_date=due_date,
                created_by_user_id=actor_user_id, updated_by_user_id=actor_user_id,
            ).returning(tasks.c.id)
        ).scalar_one()

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
