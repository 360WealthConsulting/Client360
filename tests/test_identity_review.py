"""Sprint 2 (BL-2) — Match Review queue for unresolved single-source contacts.

promote_unlinked leaves ambiguous contacts (several candidate people, or a contact detail shared
with another unlinked contact) for a human. This queue lists them and resolves each by linking to
an existing person or creating a new one — a human decision, no automatic merge thresholds.
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy import func, insert, select

from app.db import audit_events, engine, households, people, person_source_links, source_contacts
from app.matching.promote import (
    list_ambiguous_unlinked,
    resolve_create_person,
    resolve_link_to_person,
)


def _person(full_name, *, email=None, phone=None):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"H {uuid.uuid4().hex[:6]}")
                        .returning(households.c.id)).scalar_one()
        return c.execute(people.insert().values(
            household_id=hid, full_name=full_name, active=True,
            primary_email=email, normalized_email=email, primary_phone=phone,
            normalized_phone=phone).returning(people.c.id)).scalar_one()


def _contact(full_name, *, email=None, phone=None, system="wealthbox"):
    with engine.begin() as c:
        return c.execute(insert(source_contacts).values(
            source_system=system, source_file="t.csv", source_hash=uuid.uuid4().hex,
            raw_data=json.dumps({"name": full_name}), full_name=full_name,
            email=email, normalized_email=email, phone=phone, normalized_phone=phone,
        ).returning(source_contacts.c.id)).scalar_one()


def _ids(rows):
    return {r["id"] for r in rows}


def test_multiple_candidate_contact_is_listed_with_candidates():
    email = f"multi{uuid.uuid4().hex[:8]}@e.com"
    p1 = _person("Bob A", email=email)
    p2 = _person("Bob B", email=email)          # two people share the email
    sc = _contact("Bob Source", email=email)    # -> two candidates -> ambiguous
    listed = [r for r in list_ambiguous_unlinked() if r["id"] == sc]
    assert len(listed) == 1
    assert listed[0]["reason"] == "multiple_candidates"
    assert {c["id"] for c in listed[0]["candidates"]} == {p1, p2}


def test_shared_contact_info_between_unlinked_is_listed():
    phone = f"5125{uuid.uuid4().hex[:6]}"
    a = _contact("Shared One", phone=phone)
    b = _contact("Shared Two", phone=phone)     # two unlinked share a phone, no person -> ambiguous
    listed = _ids(list_ambiguous_unlinked())
    assert a in listed and b in listed
    for row in list_ambiguous_unlinked():
        if row["id"] in (a, b):
            assert row["reason"] == "shared_contact_info" and row["candidates"] == []


def test_unique_contact_is_not_listed():
    sc = _contact("Unique Person", email=f"uniq{uuid.uuid4().hex[:8]}@e.com")
    assert sc not in _ids(list_ambiguous_unlinked())   # promote would auto-create this, not ambiguous


def test_resolve_link_to_person_creates_link():
    email = f"link{uuid.uuid4().hex[:8]}@e.com"
    p1 = _person("Cand A", email=email)
    _person("Cand B", email=email)
    sc = _contact("To Link", email=email)
    resolve_link_to_person(sc, p1)
    with engine.connect() as c:
        linked = c.execute(select(person_source_links.c.person_id).where(
            person_source_links.c.source_contact_id == sc)).scalar_one()
    assert linked == p1
    assert sc not in _ids(list_ambiguous_unlinked())   # resolved -> gone from the queue


def test_resolve_create_person_creates_and_links():
    phone = f"5126{uuid.uuid4().hex[:6]}"
    _contact("Shared X", phone=phone)
    sc = _contact("Shared Y", phone=phone)
    new_pid = resolve_create_person(sc)
    with engine.connect() as c:
        person = c.execute(select(people).where(people.c.id == new_pid)).mappings().one()
        linked = c.execute(select(person_source_links.c.person_id).where(
            person_source_links.c.source_contact_id == sc)).scalar_one()
    assert person["full_name"] == "Shared Y" and linked == new_pid


def test_route_resolves_and_audits():
    import asyncio
    from urllib.parse import urlencode

    from app.routes.identity_review import resolve_contact
    from app.security.models import Principal

    email = f"route{uuid.uuid4().hex[:8]}@e.com"
    p1 = _person("Route Cand A", email=email)
    _person("Route Cand B", email=email)
    sc = _contact("Route Link", email=email)

    class _State:
        request_id = "rev-req"

    class _Req:
        def __init__(self, form):
            self._b = urlencode(form).encode()
            self.state = _State()

        async def body(self):
            return self._b

    principal = Principal(1, "s@e.com", "Staff", frozenset())
    resp = asyncio.run(resolve_contact(_Req({"action": "link", "person_id": str(p1)}), sc, principal))
    assert resp.status_code == 303
    with engine.connect() as c:
        linked = c.execute(select(person_source_links.c.person_id).where(
            person_source_links.c.source_contact_id == sc)).scalar_one()
        audited = c.execute(select(func.count()).select_from(audit_events).where(
            audit_events.c.action == "identity.contact_resolved",
            audit_events.c.entity_id == str(sc))).scalar_one()
    assert linked == p1 and audited == 1
