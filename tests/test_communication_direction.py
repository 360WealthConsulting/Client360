"""Sprint 2 — optional inbound/outbound direction on logged communications."""
from __future__ import annotations

import asyncio
import uuid
from urllib.parse import urlencode

import pytest
from sqlalchemy import select

from app.db import engine, households, people
from app.security.models import Principal
from app.services.notes import _table, add_person_note, list_person_notes


def _person():
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"D {s}").returning(households.c.id)).scalar_one()
        return c.execute(people.insert().values(
            household_id=hid, full_name=f"Dir Person {s}", active=True).returning(people.c.id)).scalar_one()


def _direction_of(note_id):
    notes = _table("person_notes")
    with engine.connect() as c:
        return c.execute(select(notes.c.direction).where(notes.c.id == note_id)).scalar_one()


def test_direction_is_stored_and_listed():
    pid = _person()
    nid = add_person_note(pid, "Client rang in about RMD.", note_type="call", direction="inbound")
    assert _direction_of(nid) == "inbound"
    assert list_person_notes(pid)[0]["direction"] == "inbound"


def test_direction_optional_defaults_none():
    pid = _person()
    nid = add_person_note(pid, "General note.", note_type="note")
    assert _direction_of(nid) is None
    assert list_person_notes(pid)[0]["direction"] is None


def test_invalid_direction_rejected():
    with pytest.raises(ValueError):
        add_person_note(_person(), "x", note_type="call", direction="sideways")


def test_route_logs_call_with_direction():
    from app.routes.notes import post_person_notes

    pid = _person()

    class _State:
        request_id = "dir-req"

    class _Req:
        def __init__(self, form):
            self._b = urlencode(form).encode()
            self.state = _State()

        async def body(self):
            return self._b

    principal = Principal(1, "s@e.com", "Staff", frozenset())
    asyncio.run(post_person_notes(_Req(
        {"kind": "activity", "note_type": "call", "direction": "outbound",
         "note": "Called client to confirm meeting."}), pid, principal))
    note = list_person_notes(pid)[0]
    assert note["note_type"] == "call" and note["direction"] == "outbound"


def test_route_ignores_direction_for_general_note():
    from app.routes.notes import post_person_notes

    pid = _person()

    class _State:
        request_id = "dir-req2"

    class _Req:
        def __init__(self, form):
            self._b = urlencode(form).encode()
            self.state = _State()

        async def body(self):
            return self._b

    principal = Principal(1, "s@e.com", "Staff", frozenset())
    asyncio.run(post_person_notes(_Req(
        {"kind": "activity", "note": "Just a note.", "direction": "inbound"}), pid, principal))
    assert list_person_notes(pid)[0]["direction"] is None  # direction ignored for general notes
