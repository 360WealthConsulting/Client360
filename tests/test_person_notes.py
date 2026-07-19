"""Task 3 (refined) — permanent client note + typed append-only person notes.

Permanent: one editable, audited note per person; legacy filesystem blobs migrate here
(idempotently, never overwritten). Person notes: a single append-only typed table
(note/call/meeting/email/task/system) reused by later features; attributed, timestamped,
searchable, and safe under simultaneous adds; each staff note records a timeline + audit event.
"""
from __future__ import annotations

import asyncio
import pathlib
import uuid
from urllib.parse import urlencode

import pytest
from sqlalchemy import func, select

from app.db import audit_events, engine, households, people, timeline_events
from app.security.models import Principal
from app.services import notes as notes_service
from app.services.notes import (
    NOTE_TYPES,
    add_person_note,
    ensure_permanent_migrated,
    get_permanent_note,
    list_person_notes,
    migrate_filesystem_notes,
    save_permanent_note,
    search_person_notes,
)

SCRATCH = pathlib.Path("/private/tmp/claude-501/-Users-mikes/5582268d-ef89-4e8b-9f85-30a00747e770/scratchpad")


def _person():
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"N {s}").returning(households.c.id)).scalar_one()
        return c.execute(people.insert().values(
            household_id=hid, full_name=f"Notes Person {s}", active=True).returning(people.c.id)).scalar_one()


def _user(name):
    from app.db import users
    with engine.begin() as c:
        return c.execute(users.insert().values(
            email=f"{uuid.uuid4().hex[:10]}@example.com", normalized_email=f"{uuid.uuid4().hex}@example.com",
            display_name=name, status="active").returning(users.c.id)).scalar_one()


class _State:
    request_id = "test-req"


class _Req:
    def __init__(self, form):
        self._b = urlencode(form).encode("utf-8")
        self.state = _State()

    async def body(self):
        return self._b


def _post(person_id, form, principal):
    from app.routes.notes import post_person_notes
    return asyncio.run(post_person_notes(_Req(form), person_id, principal))


# --- permanent note ----------------------------------------------------------

def test_permanent_note_is_one_editable_record():
    pid = _person()
    uid = _user("Advisor")
    save_permanent_note(pid, "Prefers phone calls. Two kids.", editor_user_id=uid)
    save_permanent_note(pid, "Prefers phone calls. Two kids. Retiring 2030.", editor_user_id=uid)
    cur = get_permanent_note(pid)
    assert "Retiring 2030" in cur["body"] and cur["updated_by_user_id"] == uid
    # exactly one permanent row per person (no version-history table)
    pn = notes_service._table("person_permanent_notes")
    with engine.connect() as c:
        assert c.execute(select(func.count()).select_from(pn).where(pn.c.person_id == pid)).scalar_one() == 1


def test_legacy_filesystem_note_migrates_into_permanent_note():
    pid = _person()
    tmp = SCRATCH / "notes_t3b"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / f"{pid}.txt").write_text("Legacy enduring note about the client.", encoding="utf-8")
    old = notes_service.NOTES_ROOT
    notes_service.NOTES_ROOT = tmp
    try:
        assert ensure_permanent_migrated(pid) is True
        cur = get_permanent_note(pid)
        assert cur["body"] == "Legacy enduring note about the client."   # content preserved
        assert list_person_notes(pid) == []                              # NOT activity history
        # idempotent + never overwrites the migrated permanent note
        save_permanent_note(pid, "edited by staff", editor_user_id=_user("Z"))
        assert ensure_permanent_migrated(pid) is False
        assert get_permanent_note(pid)["body"] == "edited by staff"
    finally:
        notes_service.NOTES_ROOT = old


def test_bulk_migration_is_idempotent(monkeypatch, tmp_path):
    p1, p2 = _person(), _person()
    monkeypatch.setattr(notes_service, "NOTES_ROOT", tmp_path)
    (tmp_path / f"{p1}.txt").write_text("one", encoding="utf-8")
    (tmp_path / f"{p2}.txt").write_text("two", encoding="utf-8")
    (tmp_path / "junk.txt").write_text("ignored", encoding="utf-8")
    assert migrate_filesystem_notes() == {"files": 2, "migrated": 2, "skipped": 0}
    assert migrate_filesystem_notes()["migrated"] == 0          # second run migrates nothing


# --- person notes (typed, append-only) ---------------------------------------

def test_person_note_attribution_and_append_only():
    pid = _person()
    a, b = _user("Lauren"), _user("Ops")
    add_person_note(pid, "Called about missing W-2.", author_user_id=a)
    add_person_note(pid, "Emailed the organizer.", author_user_id=b)
    rows = list_person_notes(pid)
    assert len(rows) == 2 and {r["author_name"] for r in rows} == {"Lauren", "Ops"}   # no overwrite
    assert rows[0]["created_at"] >= rows[1]["created_at"]      # newest first


def test_note_type_supports_future_reuse():
    pid = _person()
    add_person_note(pid, "Discovery call re: retirement.", author_user_id=_user("Adv"), note_type="call")
    add_person_note(pid, "Just a note.", author_user_id=_user("Adv2"), note_type="note")
    assert {"note", "call", "meeting", "email", "task", "system"} == set(NOTE_TYPES)
    calls = list_person_notes(pid, note_types=["call"])
    assert len(calls) == 1 and calls[0]["note_type"] == "call"
    with pytest.raises(ValueError):
        add_person_note(pid, "x", author_user_id=1, note_type="bogus")


def test_person_note_is_searchable_and_empty_rejected():
    pid = _person()
    token = f"ZZ{uuid.uuid4().hex[:8].upper()}"
    add_person_note(pid, f"Discussed {token}.", author_user_id=_user("Y"))
    assert any(h["person_id"] == pid for h in search_person_notes(token))
    with pytest.raises(ValueError):
        add_person_note(pid, "   ", author_user_id=_user("X"))


# --- route: timeline + audit -------------------------------------------------

def test_activity_note_route_records_timeline_and_audit():
    pid = _person()
    principal = Principal(_user("Route A"), "ra@example.com", "Route A", frozenset())
    _post(pid, {"kind": "activity", "note": "Logged a client call."}, principal)
    with engine.connect() as c:
        tl = c.execute(select(func.count()).select_from(timeline_events).where(
            timeline_events.c.person_id == pid, timeline_events.c.event_type == "activity_note_added")).scalar_one()
        au = c.execute(select(func.count()).select_from(audit_events).where(
            audit_events.c.action == "note.activity.added", audit_events.c.entity_id == str(pid))).scalar_one()
    assert tl == 1 and au == 1 and len(list_person_notes(pid)) == 1


def test_permanent_note_route_is_audited_without_timeline():
    pid = _person()
    principal = Principal(_user("Route P"), "rp@example.com", "Route P", frozenset())
    _post(pid, {"kind": "permanent", "body": "Enduring context v1."}, principal)
    _post(pid, {"kind": "permanent", "body": "Enduring context v2."}, principal)
    assert get_permanent_note(pid)["body"] == "Enduring context v2."
    with engine.connect() as c:
        au = c.execute(select(func.count()).select_from(audit_events).where(
            audit_events.c.action == "note.permanent.updated", audit_events.c.entity_id == str(pid))).scalar_one()
        tl = c.execute(select(func.count()).select_from(timeline_events).where(
            timeline_events.c.person_id == pid, timeline_events.c.event_type.like("%note%"))).scalar_one()
    assert au == 2 and tl == 0   # audited, but permanent edits are not chronological timeline events


def test_route_uses_two_note_models_not_the_old_filesystem_api():
    import inspect

    from app.routes import notes as notes_route
    src = inspect.getsource(notes_route)
    assert "save_permanent_note" in src and "add_person_note" in src
    assert "get_person_notes" not in src and "save_person_notes" not in src
