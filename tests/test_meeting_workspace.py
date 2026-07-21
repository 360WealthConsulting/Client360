"""Meeting Workspace brief tests (Phase D.3).

Covers authorization (capability + explicit person record-scope, since
/workspace/meetings/{id} is NOT covered by RECORD_PATH), event-to-person
validation (another client's / non-calendar events never surface), person-keyed
composition (no cross-client or household-member leak), the entry-point links,
and the absence of advisor-intelligence / historical-change content.
"""
import uuid
from datetime import datetime, time, timedelta

from fastapi import HTTPException
from sqlalchemy import delete, insert
from starlette.requests import Request

import app.db as d
from app.db import (
    accounts,
    engine,
    exception_types,
    exceptions,
    people,
    record_assignments,
    tasks,
    timeline_events,
    users,
)
from app.security.models import Principal
from app.services.advisor_workspace import FIRM_TZ, get_daily_dashboard, get_meeting_brief

person_notes = d.metadata.tables["person_notes"]
ADVISOR_CAPS = frozenset({"client.read", "work.read", "exception.read", "task.read"})


def _req(path="/workspace/meetings/1"):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})


def _tax_type_id(conn):
    return conn.execute(exception_types.select().where(exception_types.c.domain == "tax")).mappings().first()["id"]


def _seed_client(conn, tag, now, *, assigned_to=None):
    pid = conn.execute(people.insert().values(
        full_name=f"Client {tag}", primary_email=f"{tag}@e.test",
        normalized_email=f"{tag}@e.test", active=True).returning(people.c.id)).scalar_one()
    today = now.date()
    cal_id = conn.execute(insert(timeline_events).values(
        person_id=pid, source="microsoft", event_type="calendar_event",
        title=f"Review meeting {tag}",
        event_time=datetime.combine(today, time(10, 0), tzinfo=FIRM_TZ)).returning(timeline_events.c.id)).scalar_one()
    note_ev_id = conn.execute(insert(timeline_events).values(
        person_id=pid, source="staff", event_type="note_added",
        title=f"Note {tag}", event_time=now - timedelta(hours=1)).returning(timeline_events.c.id)).scalar_one()
    conn.execute(insert(tasks).values(person_id=pid, title=f"Task {tag}", status="open", priority="normal"))
    conn.execute(insert(exceptions).values(
        exception_type_id=_tax_type_id(conn), domain="tax", category="client", severity="high",
        status="open", title=f"Exc {tag}", source="system", opened_at=now,
        escalation_level=0, notification_count=0, person_id=pid))
    conn.execute(insert(accounts).values(
        person_id=pid, custodian="Schwab", account_number=f"ACCT-{tag}", account_name=f"Acct {tag}",
        status="open", last_review_date=None))
    conn.execute(insert(person_notes).values(person_id=pid, note_type="general", body=f"Note body {tag}"))
    if assigned_to is not None:
        conn.execute(insert(record_assignments).values(
            user_id=assigned_to, entity_type="person", entity_id=pid,
            assignment_type="owner", effective_date=today))
    return pid, cal_id, note_ev_id


def _setup():
    tag = uuid.uuid4().hex[:8]
    now = datetime.now(FIRM_TZ)
    with engine.begin() as conn:
        uid = conn.execute(users.insert().values(
            email=f"adv-{tag}@e.test", normalized_email=f"adv-{tag}@e.test",
            display_name=f"Adv {tag}", status="active").returning(users.c.id)).scalar_one()
        a, a_cal, a_note_ev = _seed_client(conn, f"A{tag}", now, assigned_to=uid)
        b, b_cal, _ = _seed_client(conn, f"B{tag}", now, assigned_to=None)
    return {"uid": uid, "a": a, "b": b, "a_cal": a_cal, "a_note_ev": a_note_ev, "b_cal": b_cal,
            "now": now, "principal": Principal(uid, "a@e.com", "Adv", ADVISOR_CAPS)}


def _teardown(ids):
    with engine.begin() as conn:
        for pid in (ids["a"], ids["b"]):
            conn.execute(delete(person_notes).where(person_notes.c.person_id == pid))
            conn.execute(delete(exceptions).where(exceptions.c.person_id == pid))
            conn.execute(delete(tasks).where(tasks.c.person_id == pid))
            conn.execute(delete(timeline_events).where(timeline_events.c.person_id == pid))
            conn.execute(delete(accounts).where(accounts.c.person_id == pid))
        conn.execute(delete(record_assignments).where(record_assignments.c.user_id == ids["uid"]))
        conn.execute(delete(people).where(people.c.id.in_((ids["a"], ids["b"]))))
        # user intentionally left (shared-DB convention; see test_advisor_workspace)


# --- authorization -----------------------------------------------------------

def test_meeting_route_capability_and_not_firm_wide():
    from app.security.middleware import FIRM_WIDE_COLLECTION, RULES
    cap = next((code for pat, code in RULES if pat.search("/workspace/meetings/1")), None)
    assert cap == "client.read"
    assert not FIRM_WIDE_COLLECTION.match("/workspace/meetings/1")


def test_inaccessible_person_is_404():
    from app.routes.workspace import meeting_brief
    ids = _setup()
    try:
        # Advisor is assigned to A but not B -> B is 404 (record_in_scope false).
        try:
            meeting_brief(_req(), ids["b"], None, principal=ids["principal"])
            raise AssertionError("expected 404 for inaccessible person")
        except HTTPException as exc:
            assert exc.status_code == 404
        # A is accessible -> renders.
        resp = meeting_brief(_req(), ids["a"], None, principal=ids["principal"])
        assert resp.status_code == 200
        assert "Meeting brief" in resp.body.decode()
    finally:
        _teardown(ids)


# --- event-to-person validation ----------------------------------------------

def test_valid_calendar_event_context():
    ids = _setup()
    try:
        brief = get_meeting_brief(ids["a"], event_id=ids["a_cal"])
        assert brief["meeting_event"] is not None
        assert brief["meeting_event"]["event_type"] == "calendar_event"
        assert brief["meeting_event"]["person_id"] == ids["a"]
    finally:
        _teardown(ids)


def test_event_of_another_person_is_omitted():
    ids = _setup()
    try:
        # B's calendar event must NEVER surface on A's brief.
        brief = get_meeting_brief(ids["a"], event_id=ids["b_cal"])
        assert brief["meeting_event"] is None
    finally:
        _teardown(ids)


def test_non_calendar_event_is_omitted():
    ids = _setup()
    try:
        brief = get_meeting_brief(ids["a"], event_id=ids["a_note_ev"])  # a note_added event
        assert brief["meeting_event"] is None
    finally:
        _teardown(ids)


def test_general_brief_without_event():
    ids = _setup()
    try:
        brief = get_meeting_brief(ids["a"])
        assert brief["meeting_event"] is None
        assert brief["snapshot"]["person_id"] == ids["a"]
    finally:
        _teardown(ids)


# --- person-keyed composition, no leak ---------------------------------------

def test_brief_is_person_keyed_no_cross_client_leak():
    ids = _setup()
    a, b = ids["a"], ids["b"]
    try:
        brief = get_meeting_brief(a)
        # A's data present.
        assert any(t["person_id"] == a for t in brief["open_tasks"])
        assert any(e["person_id"] == a for e in brief["open_exceptions"])
        assert any(r["person_id"] == a for r in brief["reviews"])
        assert any(ev["person_id"] == a for ev in brief["activity"])
        # B (inaccessible) never appears in any list.
        for panel in ("open_tasks", "open_exceptions", "reviews", "activity", "notes"):
            assert all(row.get("person_id") != b for row in brief[panel]), f"{panel} leaked B"
    finally:
        _teardown(ids)


def test_snapshot_reused_and_current_values_labeled():
    from app.routes.workspace import meeting_brief
    ids = _setup()
    try:
        body = meeting_brief(_req(), ids["a"], None, principal=ids["principal"]).body.decode()
        assert "Client 360" in body and "Client AUM" in body
        # Financial section labelled current values, not historical change.
        assert "current values" in body.lower()
    finally:
        _teardown(ids)


def test_no_advisor_intelligence_or_historical_change():
    from app.routes.workspace import meeting_brief
    ids = _setup()
    try:
        body = meeting_brief(_req(), ids["a"], None, principal=ids["principal"]).body.decode().lower()
        for banned in ("recommend", "roth", "conversion", "cross-sell", "coverage gap",
                       "suitab", "retirement readiness", "since last meeting", "performance",
                       "rate of return"):
            assert banned not in body, f"banned content: {banned}"
    finally:
        _teardown(ids)


# --- entry points ------------------------------------------------------------

def test_dashboard_meeting_link_points_to_meeting_workspace():
    ids = _setup()
    try:
        d_ = get_daily_dashboard(ids["principal"], now=ids["now"])
        mine = [m for m in d_["meetings"] if m["person_id"] == ids["a"]]
        assert mine, "expected today's meeting for A"
        assert mine[0]["link"] == f"/workspace/meetings/{ids['a']}?event={ids['a_cal']}"
    finally:
        _teardown(ids)


def test_overview_links_to_meeting_workspace():
    from app.routes.people import person_profile
    ids = _setup()
    try:
        req = _req(path=f"/people/{ids['a']}")
        req.state.principal = Principal(1, "x@e.com", "X",
                                        frozenset({"client.read", "record.read_all", "work.read", "exception.read"}))
        body = person_profile(req, ids["a"], tab="overview").body.decode()
        assert f"/workspace/meetings/{ids['a']}" in body
    finally:
        _teardown(ids)
