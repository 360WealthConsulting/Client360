"""Sprint 2 (Release Readiness) — household detail roll-up: member count, aggregate AUM,
and open tasks across members.
"""
from __future__ import annotations

import uuid

from sqlalchemy import insert
from starlette.requests import Request

from app.db import accounts, engine, household_relationships, households, people, tasks
from app.routes.households import household_profile
from app.security.models import Principal


def _household(name):
    with engine.begin() as c:
        return c.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()


def _member(household_id, name):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            household_id=household_id, full_name=name, active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(household_relationships).values(
            household_id=household_id, person_id=pid, relationship_type="member"))
        return pid


def _account(household_id, person_id, value):
    with engine.begin() as c:
        c.execute(insert(accounts).values(
            household_id=household_id, person_id=person_id, account_number=uuid.uuid4().hex[:12],
            custodian="Schwab", total_value=value))


def _open_task(person_id, title):
    with engine.begin() as c:
        c.execute(insert(tasks).values(person_id=person_id, title=title, status="open", priority="normal"))


def _rollup(household_id):
    scope = {"type": "http", "method": "GET", "path": f"/households/{household_id}",
             "headers": [], "query_string": b""}
    req = Request(scope)
    req.state.principal = Principal(1, "s@e.com", "Staff", frozenset({"record.read_all"}))
    resp = household_profile(req, household_id)
    return resp.context["rollup"]


def test_rollup_aggregates_members_aum_and_open_tasks():
    hid = _household(f"Hawthorne {uuid.uuid4().hex[:6]}")
    p1 = _member(hid, "Alex Hawthorne")
    p2 = _member(hid, "Blair Hawthorne")
    _account(hid, p1, 100000)
    _account(hid, p2, 50000.50)
    _open_task(p1, "Review beneficiaries")
    _open_task(p2, "Send statement")
    with engine.begin() as c:  # a completed task must not count
        c.execute(insert(tasks).values(person_id=p1, title="done", status="complete", priority="normal"))

    rollup = _rollup(hid)
    assert rollup["member_count"] == 2
    assert float(rollup["household_aum"]) == 150000.50
    assert rollup["open_task_count"] == 2


def test_empty_household_rollup_is_zeroed():
    hid = _household(f"Empty {uuid.uuid4().hex[:6]}")
    rollup = _rollup(hid)
    assert rollup["member_count"] == 0
    assert float(rollup["household_aum"]) == 0
    assert rollup["open_task_count"] == 0
