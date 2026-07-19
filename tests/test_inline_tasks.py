"""Task 4 — inline tasks (reusing the existing ``tasks`` table).

Covers create/complete via the shared service (actor attribution, timeline + audit, resubmit
dedupe) and the activity-note -> optional follow-up task path.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date
from urllib.parse import urlencode

import pytest
from sqlalchemy import func, select

from app.db import (
    audit_events,
    engine,
    households,
    people,
    record_assignments,
    tasks,
    timeline_events,
)
from app.security.models import Principal
from app.services.tasks import complete_task, create_task


def _person():
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"T {s}").returning(households.c.id)).scalar_one()
        return c.execute(people.insert().values(
            household_id=hid, full_name=f"Task Person {s}", active=True).returning(people.c.id)).scalar_one()


def _user(name="Staff"):
    from app.db import users
    with engine.begin() as c:
        return c.execute(users.insert().values(
            email=f"{uuid.uuid4().hex[:10]}@example.com", normalized_email=f"{uuid.uuid4().hex}@example.com",
            display_name=name, status="active").returning(users.c.id)).scalar_one()


def _task(tid):
    with engine.connect() as c:
        return c.execute(select(tasks).where(tasks.c.id == tid)).mappings().one()


def _count(table, **where):
    with engine.connect() as c:
        q = select(func.count()).select_from(table)
        for k, v in where.items():
            q = q.where(getattr(table.c, k) == v)
        return c.execute(q).scalar_one()


# --- create ------------------------------------------------------------------

def test_create_task_assigns_via_user_model_not_free_text():
    pid, actor, assignee = _person(), _user("Actor"), _user("Assignee")
    tid = create_task(pid, title="Send RMD reminder", priority="high",
                      assigned_to_user_id=assignee, due_date=date(2026, 12, 1),
                      actor_user_id=actor, request_id="req-1")
    row = _task(tid)
    assert row["status"] == "open" and row["priority"] == "high"
    assert row["due_date"] == date(2026, 12, 1) and row["person_id"] == pid
    assert row["created_by_user_id"] == actor
    assert row["assigned_to"] is None                           # legacy free-text field NOT used
    # assignment recorded in the canonical record_assignments model, referencing a real user
    with engine.connect() as c:
        a = c.execute(select(record_assignments).where(
            record_assignments.c.entity_type == "task", record_assignments.c.entity_id == tid,
            record_assignments.c.assignment_type == "primary",
            record_assignments.c.inactive_date.is_(None))).mappings().all()
    assert len(a) == 1 and a[0]["user_id"] == assignee
    assert _count(timeline_events, person_id=pid, event_type="task_created") == 1
    assert _count(audit_events, action="task.created", entity_id=str(tid)) == 1


def test_create_task_requires_title_valid_person_and_real_assignee():
    with pytest.raises(ValueError):
        create_task(_person(), title="   ", actor_user_id=_user())
    with pytest.raises(ValueError):
        create_task(999_999_999, title="orphan", actor_user_id=_user())
    with pytest.raises(ValueError):   # arbitrary/non-existent assignee is rejected
        create_task(_person(), title="bad assignee", assigned_to_user_id=999_999_999, actor_user_id=_user())


def test_duplicate_resubmission_is_suppressed():
    pid, uid = _person(), _user()
    t1 = create_task(pid, title="Call about beneficiary", actor_user_id=uid, request_id="r")
    t2 = create_task(pid, title="Call about beneficiary", actor_user_id=uid, request_id="r")  # rapid resubmit
    assert t1 is not None and t2 is None                        # second suppressed
    assert _count(tasks, person_id=pid, title="Call about beneficiary") == 1


# --- complete ----------------------------------------------------------------

def test_complete_task_records_timeline_and_audit_and_is_no_op_when_repeated():
    pid, uid = _person(), _user()
    tid = create_task(pid, title="Confirm address", actor_user_id=uid)
    assert complete_task(pid, tid, actor_user_id=uid, request_id="c1") is True
    assert _task(tid)["status"] == "complete" and _task(tid)["updated_by_user_id"] == uid
    assert _count(timeline_events, person_id=pid, event_type="task_completed") == 1
    assert _count(audit_events, action="task.completed", entity_id=str(tid)) == 1
    # completing again is a no-op (no duplicate timeline/audit)
    assert complete_task(pid, tid, actor_user_id=uid, request_id="c2") is True
    assert _count(timeline_events, person_id=pid, event_type="task_completed") == 1
    assert complete_task(pid, 999_999_999, actor_user_id=uid) is False


# --- activity note -> follow-up task -----------------------------------------

class _State:
    request_id = "req-note"


class _Req:
    def __init__(self, form):
        self._b = urlencode(form).encode("utf-8")
        self.state = _State()

    async def body(self):
        return self._b


def test_activity_note_creates_optional_follow_up_task():
    from app.routes.notes import post_person_notes
    pid, uid, assignee = _person(), _user("Author"), _user("Assignee2")
    principal = Principal(uid, "a@example.com", "Author", frozenset())
    form = {"kind": "activity", "note": "Client asked us to review their 401k.",
            "create_task": "1", "task_title": "Review 401k allocation", "task_priority": "high",
            "task_assigned_to_user_id": str(assignee)}
    asyncio.run(post_person_notes(_Req(form), pid, principal))
    # one activity note + one follow-up task, both attributed
    from app.services.notes import list_person_notes
    assert len(list_person_notes(pid)) == 1
    with engine.connect() as c:
        task_rows = [dict(r) for r in c.execute(
            select(tasks).where(tasks.c.person_id == pid)).mappings().all()]
        assign = c.execute(select(record_assignments.c.user_id).where(
            record_assignments.c.entity_type == "task", record_assignments.c.entity_id == task_rows[0]["id"],
            record_assignments.c.inactive_date.is_(None))).scalar()
    assert len(task_rows) == 1 and task_rows[0]["title"] == "Review 401k allocation"
    assert task_rows[0]["priority"] == "high" and task_rows[0]["created_by_user_id"] == uid
    assert assign == assignee                                   # follow-up task assigned to the real user
    assert _count(audit_events, action="task.created", entity_id=str(task_rows[0]["id"])) == 1


def test_activity_note_without_task_flag_creates_no_task():
    from app.routes.notes import post_person_notes
    pid = _person()
    principal = Principal(_user(), "b@example.com", "B", frozenset())
    asyncio.run(post_person_notes(_Req({"kind": "activity", "note": "Just a note.",
                                        "task_title": "ignored because no create_task flag"}), pid, principal))
    assert _count(tasks, person_id=pid) == 0
