"""Sprint 2 (D-4) — the assignee picker is scoped to provisioned staff (active users
holding an active role), not every active user row.
"""
from __future__ import annotations

import uuid

from sqlalchemy import insert, select

from app.db import engine, roles, user_roles, users
from app.services.tasks import assignable_users


def _user(name, *, status="active"):
    with engine.begin() as c:
        return c.execute(users.insert().values(
            email=f"{uuid.uuid4().hex[:10]}@e.com", normalized_email=f"{uuid.uuid4().hex}@e.com",
            display_name=name, status=status).returning(users.c.id)).scalar_one()


def _grant_role(user_id, code="administrator", *, inactive=False):
    with engine.begin() as c:
        role_id = c.execute(select(roles.c.id).where(roles.c.code == code)).scalar_one()
        values = {"user_id": user_id, "role_id": role_id}
        if inactive:
            values["inactive_date"] = "2020-01-01"
        c.execute(insert(user_roles).values(**values))


def _ids():
    return {u["id"] for u in assignable_users()}


def test_user_with_active_role_is_assignable():
    uid = _user(f"Staff {uuid.uuid4().hex[:5]}")
    _grant_role(uid)
    assert uid in _ids()


def test_active_user_without_a_role_is_not_assignable():
    uid = _user(f"Orphan {uuid.uuid4().hex[:5]}")
    assert uid not in _ids()


def test_user_with_only_an_inactive_role_is_not_assignable():
    uid = _user(f"Former {uuid.uuid4().hex[:5]}")
    _grant_role(uid, inactive=True)
    assert uid not in _ids()


def test_user_with_multiple_roles_appears_once():
    uid = _user(f"Multi {uuid.uuid4().hex[:5]}")
    _grant_role(uid, code="administrator")
    _grant_role(uid, code="advisor")
    rows = [u for u in assignable_users() if u["id"] == uid]
    assert len(rows) == 1
