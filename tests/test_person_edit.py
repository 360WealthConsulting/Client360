"""Sprint 2 — staff-editable canonical person contact/address fields.

Edits update the people record, keep normalized_email/phone in sync, record a timeline +
audit event (field names only), and only touch fields that actually changed.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.db import audit_events, engine, households, people, timeline_events
from app.services.people import update_person_contact


def _person(**cols):
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"E {s}").returning(households.c.id)).scalar_one()
        values = {"household_id": hid, "full_name": f"Edit Person {s}", "active": True}
        values.update(cols)
        return c.execute(people.insert().values(**values).returning(people.c.id)).scalar_one()


def _get(pid):
    with engine.connect() as c:
        return c.execute(select(people).where(people.c.id == pid)).mappings().one()


def _count(table, **where):
    with engine.connect() as c:
        q = select(func.count()).select_from(table)
        for k, v in where.items():
            q = q.where(getattr(table.c, k) == v)
        return c.execute(q).scalar_one()


def test_update_changes_fields_and_normalizes_email_phone():
    pid = _person(primary_email="old@example.com", city="Dallas")
    changed = update_person_contact(pid, {
        "primary_email": "New.Client@Example.com", "primary_phone": "(512) 555-0199",
        "city": "Austin", "state": "TX",
    }, actor_user_id=1, request_id="r1")
    assert set(changed) == {"primary_email", "primary_phone", "city", "state"}
    row = _get(pid)
    assert row["primary_email"] == "New.Client@Example.com"
    assert row["normalized_email"] == "new.client@example.com"     # normalized in sync
    assert row["normalized_phone"] == "5125550199"                  # digits only
    assert row["city"] == "Austin" and row["state"] == "TX"
    # timeline + audit recorded, with field names only
    assert _count(timeline_events, person_id=pid, event_type="person_updated") == 1
    assert _count(audit_events, action="person.updated", entity_id=str(pid)) == 1


def test_no_op_update_records_nothing():
    pid = _person(city="Austin")
    changed = update_person_contact(pid, {"city": "Austin"}, actor_user_id=1)
    assert changed == []
    assert _count(timeline_events, person_id=pid, event_type="person_updated") == 0
    assert _count(audit_events, action="person.updated", entity_id=str(pid)) == 0


def test_full_name_is_not_editable_here():
    pid = _person(full_name="Original Name")
    update_person_contact(pid, {"full_name": "Hacked Name", "city": "Reno"}, actor_user_id=1)
    assert _get(pid)["full_name"] == "Original Name"        # identity field untouched


def test_missing_person_raises():
    import pytest
    with pytest.raises(ValueError):
        update_person_contact(999_999_999, {"city": "X"}, actor_user_id=1)


def test_route_updates_and_redirects():
    import asyncio
    from urllib.parse import urlencode

    from app.routes.person_edit import edit_person_submit
    from app.security.models import Principal

    pid = _person(primary_email="a@b.com")

    class _State:
        request_id = "edit-req"

    class _Req:
        def __init__(self, form):
            self._b = urlencode(form).encode()
            self.state = _State()

        async def body(self):
            return self._b

    principal = Principal(1, "s@e.com", "Staff", frozenset())
    resp = asyncio.run(edit_person_submit(_Req({"primary_email": "c@d.com", "city": "Austin"}), pid, principal))
    assert resp.status_code == 303 and resp.headers["location"] == f"/people/{pid}?saved=1"
    assert _get(pid)["primary_email"] == "c@d.com"
