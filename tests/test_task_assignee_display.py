"""UX-1 regression — task-assignee display must be identical on the Client Profile Tasks tab
and the dedicated Tasks page, because both resolve through the single canonical service
``tasks_with_assignee`` (record_assignments), with the legacy free-text field kept only as a
fallback for historical rows.
"""
from __future__ import annotations

import uuid

from sqlalchemy import insert
from starlette.requests import Request

from app.db import engine, households, people, tasks
from app.routes.people import person_profile
from app.routes.tasks import person_tasks
from app.security.models import Principal
from app.services.tasks import create_task


def _person():
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"H {s}").returning(households.c.id)).scalar_one()
        return c.execute(people.insert().values(
            household_id=hid, full_name=f"Client {s}", active=True).returning(people.c.id)).scalar_one()


def _user(name):
    from app.db import users
    with engine.begin() as c:
        return c.execute(users.insert().values(
            email=f"{uuid.uuid4().hex[:10]}@e.com", normalized_email=f"{uuid.uuid4().hex}@e.com",
            display_name=name, status="active").returning(users.c.id)).scalar_one()


def _legacy_task(person_id, assigned_to):
    """A historical task that predates canonical assignment: only the legacy free-text field set."""
    with engine.begin() as c:
        return c.execute(insert(tasks).values(
            person_id=person_id, title="Legacy task", status="open", priority="normal",
            assigned_to=assigned_to).returning(tasks.c.id)).scalar_one()


def _display(row):
    """The exact fallback both templates render."""
    return row["assignee_name"] or row["assigned_to"] or "Unassigned"


def _profile_tasks(person_id, principal):
    scope = {"type": "http", "method": "GET", "path": f"/people/{person_id}", "headers": [], "query_string": b""}
    req = Request(scope)
    req.state.principal = principal
    resp = person_profile(req, person_id, tab="tasks")
    return {t["id"]: t for t in resp.context["all_tasks"]}


def _dedicated_tasks(person_id, principal):
    scope = {"type": "http", "method": "GET", "path": f"/people/{person_id}/tasks", "headers": [], "query_string": b""}
    req = Request(scope)
    req.state.principal = principal
    resp = person_tasks(req, person_id, principal)
    return {t["id"]: t for t in resp.context["tasks"]}


def test_assignee_display_is_consistent_across_both_client_views():
    principal = Principal(_user("Viewer"), "v@e.com", "Viewer", frozenset({"record.read_all"}))
    pid = _person()
    assignee = _user("Dana Ortiz")

    canonical_id = create_task(pid, title="Prepare QCD paperwork", assigned_to_user_id=assignee,
                               actor_user_id=principal.user_id, request_id="r1")
    legacy_id = _legacy_task(pid, assigned_to="Bob Historical")
    unassigned_id = create_task(pid, title="Review beneficiary form", actor_user_id=principal.user_id, request_id="r2")

    profile = _profile_tasks(pid, principal)
    dedicated = _dedicated_tasks(pid, principal)

    # all three open tasks appear in both views
    for tid in (canonical_id, legacy_id, unassigned_id):
        assert tid in profile and tid in dedicated

    # (a) canonically assigned -> the real user's name, identical on both screens (never "Unassigned")
    assert _display(profile[canonical_id]) == "Dana Ortiz"
    assert _display(dedicated[canonical_id]) == "Dana Ortiz"
    assert profile[canonical_id]["assigned_to"] is None  # legacy free-text never written

    # (b) historical legacy-only task -> its legacy assignee, identical on both screens
    assert _display(profile[legacy_id]) == "Bob Historical"
    assert _display(dedicated[legacy_id]) == "Bob Historical"

    # (c) actually unassigned -> "Unassigned", identical on both screens
    assert _display(profile[unassigned_id]) == "Unassigned"
    assert _display(dedicated[unassigned_id]) == "Unassigned"

    # the invariant: every task's displayed assignee matches across the two views
    for tid in (canonical_id, legacy_id, unassigned_id):
        assert _display(profile[tid]) == _display(dedicated[tid])
