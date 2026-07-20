"""Task 5 — one-click communication logging (Log Call / Email / Meeting).

Reuses the typed ``person_notes`` model (note_type call/email/meeting) and the Task 4 task
service for optional follow-up tasks — no separate communication tables. Covers: a logged
communication becomes a typed person note + timeline + audit event; it appears in the activity
feed; an optional follow-up task is created via the canonical user-assignment model; an unknown
type falls back to a plain note; and the existing plain activity-note path is unchanged.
"""
from __future__ import annotations

import asyncio
import uuid
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
from app.services.notes import list_person_notes


def _person():
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"C {s}").returning(households.c.id)).scalar_one()
        return c.execute(people.insert().values(
            household_id=hid, full_name=f"Comm Person {s}", active=True).returning(people.c.id)).scalar_one()


def _user(name="Staff"):
    from app.db import users
    with engine.begin() as c:
        return c.execute(users.insert().values(
            email=f"{uuid.uuid4().hex[:10]}@example.com", normalized_email=f"{uuid.uuid4().hex}@example.com",
            display_name=name, status="active").returning(users.c.id)).scalar_one()


def _count(table, **where):
    with engine.connect() as c:
        q = select(func.count()).select_from(table)
        for k, v in where.items():
            q = q.where(getattr(table.c, k) == v)
        return c.execute(q).scalar_one()


class _State:
    request_id = "req-comm"


class _Req:
    def __init__(self, form):
        self._b = urlencode(form).encode("utf-8")
        self.state = _State()

    async def body(self):
        return self._b


def _post(pid, form, uid):
    from app.routes.notes import post_person_notes
    principal = Principal(uid, "s@example.com", "Staff", frozenset())
    return asyncio.run(post_person_notes(_Req(form), pid, principal))


# --- logging a communication -------------------------------------------------

@pytest.mark.parametrize("kind", ["call", "email", "meeting"])
def test_logged_communication_is_typed_note_with_timeline_and_audit(kind):
    pid, uid = _person(), _user()
    body = f"Discussed portfolio during {kind}."
    resp = _post(pid, {"kind": "activity", "note_type": kind, "note": body}, uid)
    assert resp.status_code == 303 and resp.headers["location"].endswith("saved=logged")

    # one typed person note, visible in the shared activity feed
    notes = list_person_notes(pid)
    assert len(notes) == 1 and notes[0]["note_type"] == kind and notes[0]["body"] == body

    # exactly one timeline event and one audit event (no duplication)
    assert _count(timeline_events, person_id=pid, event_type="communication_logged") == 1
    assert _count(audit_events, action="communication.logged", entity_id=str(pid)) == 1


def test_logged_communication_can_create_follow_up_task_via_user_model():
    pid, uid, assignee = _person(), _user("Author"), _user("Assignee")
    form = {"kind": "activity", "note_type": "call", "note": "Client wants a Roth review.",
            "create_task": "1", "task_title": "Prepare Roth analysis", "task_priority": "high",
            "task_assigned_to_user_id": str(assignee)}
    _post(pid, form, uid)
    with engine.connect() as c:
        task_rows = c.execute(select(tasks).where(tasks.c.person_id == pid)).mappings().all()
        assign = c.execute(select(record_assignments.c.user_id).where(
            record_assignments.c.entity_type == "task", record_assignments.c.entity_id == task_rows[0]["id"],
            record_assignments.c.inactive_date.is_(None))).scalar()
    assert len(task_rows) == 1 and task_rows[0]["title"] == "Prepare Roth analysis"
    assert task_rows[0]["assigned_to"] is None          # canonical model, not free text
    assert assign == assignee


def test_unknown_note_type_falls_back_to_plain_activity_note():
    pid, uid = _person(), _user()
    _post(pid, {"kind": "activity", "note_type": "smtp_hack", "note": "coerced"}, uid)
    notes = list_person_notes(pid)
    assert len(notes) == 1 and notes[0]["note_type"] == "note"
    assert _count(timeline_events, person_id=pid, event_type="activity_note_added") == 1
    assert _count(timeline_events, person_id=pid, event_type="communication_logged") == 0


def test_plain_activity_note_path_unchanged():
    pid, uid = _person(), _user()
    resp = _post(pid, {"kind": "activity", "note": "Just a general note."}, uid)
    assert resp.status_code == 303 and resp.headers["location"].endswith("saved=activity")
    assert list_person_notes(pid)[0]["note_type"] == "note"
    assert _count(audit_events, action="note.activity.added", entity_id=str(pid)) == 1
    assert _count(audit_events, action="communication.logged", entity_id=str(pid)) == 0


def test_empty_body_creates_nothing():
    pid, uid = _person(), _user()
    _post(pid, {"kind": "activity", "note_type": "call", "note": "   "}, uid)
    assert list_person_notes(pid) == []
    assert _count(timeline_events, person_id=pid, event_type="communication_logged") == 0
