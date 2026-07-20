"""Sprint 2 — task-submission idempotency.

A per-submission idempotency key makes a resubmitted create-task form a no-op: the same key
never creates a second task and never emits duplicate timeline/audit events, independently of the
short-window heuristic. Covers the service, the DB unique constraint, and a double-submitted POST.
"""
from __future__ import annotations

import asyncio
import uuid
from urllib.parse import urlencode

import pytest
from sqlalchemy import func, select

from app.db import audit_events, engine, households, people, tasks, timeline_events
from app.security.models import Principal
from app.services.tasks import create_task


def _person():
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"I {s}").returning(households.c.id)).scalar_one()
        return c.execute(people.insert().values(
            household_id=hid, full_name=f"Idem Person {s}", active=True).returning(people.c.id)).scalar_one()


def _count(table, **where):
    with engine.connect() as c:
        q = select(func.count()).select_from(table)
        for k, v in where.items():
            q = q.where(getattr(table.c, k) == v)
        return c.execute(q).scalar_one()


def test_same_key_is_idempotent_even_with_different_titles():
    # Different titles so the person+title heuristic cannot fire — this isolates the key.
    pid = _person()
    key = uuid.uuid4().hex
    first = create_task(pid, title="Prepare packet", idempotency_key=key, actor_user_id=1, request_id="r")
    second = create_task(pid, title="Totally different title", idempotency_key=key, actor_user_id=1, request_id="r")
    assert first is not None and second is None
    assert _count(tasks, person_id=pid) == 1
    # exactly one timeline + audit event (the suppressed insert emits nothing)
    assert _count(timeline_events, person_id=pid, event_type="task_created") == 1
    assert _count(audit_events, action="task.created", entity_id=str(first)) == 1


def test_different_keys_create_distinct_tasks():
    pid = _person()
    a = create_task(pid, title="Alpha task", idempotency_key=uuid.uuid4().hex, actor_user_id=1)
    b = create_task(pid, title="Beta task", idempotency_key=uuid.uuid4().hex, actor_user_id=1)
    assert a is not None and b is not None and a != b
    assert _count(tasks, person_id=pid) == 2


def test_no_key_still_creates_and_heuristic_still_guards():
    pid = _person()
    first = create_task(pid, title="No key task", actor_user_id=1)
    dup = create_task(pid, title="No key task", actor_user_id=1)   # heuristic (person+title+20s)
    assert first is not None and dup is None
    assert _count(tasks, person_id=pid) == 1


def test_unique_index_rejects_duplicate_key_at_db_level():
    from sqlalchemy.exc import IntegrityError
    pid = _person()
    key = uuid.uuid4().hex
    with engine.begin() as c:
        c.execute(tasks.insert().values(person_id=pid, title="x", status="open",
                                        priority="normal", idempotency_key=key))
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(tasks.insert().values(person_id=pid, title="y", status="open",
                                            priority="normal", idempotency_key=key))


def test_double_submitted_post_creates_one_task():
    from app.routes.tasks import create_person_task

    pid = _person()
    key = uuid.uuid4().hex
    form = {"title": "Follow up with client", "priority": "normal", "idempotency_key": key}

    class _State:
        request_id = "idem-req"

    class _Req:
        def __init__(self, f):
            self._b = urlencode(f).encode()
            self.state = _State()

        async def body(self):
            return self._b

    principal = Principal(1, "s@e.com", "Staff", frozenset())
    r1 = asyncio.run(create_person_task(_Req(form), pid, principal))
    r2 = asyncio.run(create_person_task(_Req(form), pid, principal))   # resubmit, same token
    assert r1.status_code == 303 and r2.status_code == 303            # UX preserved on both
    assert _count(tasks, person_id=pid) == 1                          # but only one task
