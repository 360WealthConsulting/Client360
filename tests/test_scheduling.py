"""Scheduling & Meeting Management platform tests (Phase D.19).

Covers meeting CRUD, meeting templates + template-driven creation, scheduling resources +
booking/conflict, deterministic availability metadata (incl. the Microsoft 365 overlay reuse),
attendees + responses + check-in, agenda, follow-ups, the meeting lifecycle state machine +
invalid transitions + reschedule + outcome, authorization + person/household/organization record
scope, cross-domain references (Communications conversation, Opportunity/Annual Review/Workflow/
Documents/Advisor Work FK targets), Timeline lifecycle events, Analytics consumption
(``upcoming_meetings``), the append-only audit ledger, and architecture invariants. The Microsoft
365 calendar sync, notification ledger, timeline projection, Advisor Workspace, Communications,
Workflow, and the D.5 golden are untouched.
"""
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, insert, select, update

from app.db import (
    engine,
    meeting_templates,
    meetings,
    people,
    record_assignments,
    relationship_entities,
    scheduling_events,
    scheduling_resources,
    timeline_events,
    users,
)
from app.security.models import Principal
from app.services.analytics import sources
from app.services.scheduling import availability as avail
from app.services.scheduling import service as svc
from app.services.scheduling import templates as tmpl

CAPS = frozenset({"scheduling.view", "scheduling.manage", "scheduling.templates",
                  "scheduling.audit", "scheduling.admin", "record.read_all", "record.write_all"})


def _sfx():
    return uuid.uuid4().hex[:8]


def _principal(uid, caps=CAPS):
    return Principal(uid, "a@e.test", "A", frozenset(caps))


def _future(hours=24):
    return datetime.now(UTC) + timedelta(hours=hours)


def _setup(*, with_org=False):
    tag = _sfx()
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"sc-{tag}@e.test", normalized_email=f"sc-{tag}@e.test",
            display_name=f"U {tag}", status="active").returning(users.c.id)).scalar_one()
        stranger = c.execute(users.insert().values(
            email=f"str-{tag}@e.test", normalized_email=f"str-{tag}@e.test",
            display_name=f"S {tag}", status="active").returning(users.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"Client {tag}", primary_email=f"{tag}@e.test",
            normalized_email=f"{tag}@e.test", active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(record_assignments).values(
            user_id=uid, entity_type="person", entity_id=pid, assignment_type="owner",
            effective_date=date.today()))
        org_id = None
        if with_org:
            org_id = c.execute(relationship_entities.insert().values(
                entity_type="organization", name=f"Org {tag}").returning(
                relationship_entities.c.id)).scalar_one()
            c.execute(insert(record_assignments).values(
                user_id=uid, entity_type="organization", entity_id=org_id,
                assignment_type="owner", effective_date=date.today()))
    return {"uid": uid, "stranger": stranger, "pid": pid, "org_id": org_id, "tag": tag}


def _teardown(ids):
    # scheduling_events is append-only (trigger-blocked) and RESTRICT-anchors the meeting, so
    # meetings cannot be deleted: detach mutable anchors and leave them as leftovers.
    with engine.begin() as c:
        c.execute(update(meetings).where(meetings.c.person_id == ids["pid"]).values(person_id=None))
        if ids.get("org_id"):
            c.execute(update(meetings).where(meetings.c.organization_id == ids["org_id"])
                      .values(organization_id=None))
        c.execute(delete(timeline_events).where(timeline_events.c.source == "scheduling",
                                                timeline_events.c.person_id == ids["pid"]))
        c.execute(delete(record_assignments).where(record_assignments.c.entity_id == ids["pid"],
                                                   record_assignments.c.entity_type == "person"))
        if ids.get("org_id"):
            c.execute(delete(record_assignments).where(
                record_assignments.c.entity_id == ids["org_id"],
                record_assignments.c.entity_type == "organization"))
        c.execute(delete(scheduling_resources).where(scheduling_resources.c.code.like(f"r-{ids['tag']}%")))
        c.execute(delete(meeting_templates).where(meeting_templates.c.code.like(f"t-{ids['tag']}%")))
        c.execute(delete(people).where(people.c.id == ids["pid"]))
        if ids.get("org_id"):
            c.execute(delete(relationship_entities).where(relationship_entities.c.id == ids["org_id"]))
        c.execute(delete(users).where(users.c.id.in_((ids["uid"], ids["stranger"]))))


# --- meeting CRUD ------------------------------------------------------------

def test_create_and_get_meeting():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        m = svc.create_meeting(p, subject="Kickoff", meeting_type="discovery", category="prospect",
                               person_id=ids["pid"], starts_at=_future(), actor_user_id=ids["uid"])
        assert m["id"] and m["status"] == "scheduled"
        assert m["organizer_user_id"] == ids["uid"]
        detail = svc.get_meeting(p, m["id"])
        assert detail["subject"] == "Kickoff"
        assert detail["attendees"] == [] and detail["followups"] == []
    finally:
        _teardown(ids)


def test_list_meetings_filters_and_upcoming():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        svc.create_meeting(p, subject="Future review", meeting_type="annual_review",
                           person_id=ids["pid"], starts_at=_future(48), actor_user_id=ids["uid"])
        svc.create_meeting(p, subject="Draft plan", meeting_type="tax_planning", status="draft",
                           person_id=ids["pid"], actor_user_id=ids["uid"])
        assert svc.list_meetings(p)["total"] >= 2
        up = svc.list_meetings(p, upcoming_only=True)
        assert all(r["status"] in ("scheduled", "confirmed") for r in up["rows"])
        assert any("Future review" in r["subject"] for r in up["rows"])
        typed = svc.list_meetings(p, meeting_type="tax_planning")
        assert all(r["meeting_type"] == "tax_planning" for r in typed["rows"])
    finally:
        _teardown(ids)


def test_update_meeting():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        m = svc.create_meeting(p, subject="Edit me", person_id=ids["pid"], starts_at=_future(),
                               actor_user_id=ids["uid"])
        updated = svc.update_meeting(p, m["id"], subject="Edited", location="Room 5",
                                     actor_user_id=ids["uid"])
        assert updated["subject"] == "Edited" and updated["location"] == "Room 5"
    finally:
        _teardown(ids)


# --- templates ---------------------------------------------------------------

def test_template_crud_and_meeting_from_template_applies_defaults():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        code = f"t-{ids['tag']}"
        t = tmpl.create_template(code=code, name="Review", meeting_type="annual_review",
                                 category="review", default_duration_minutes=90,
                                 default_location_type="in_person",
                                 agenda=["Portfolio", "Goals"], actor_user_id=ids["uid"])
        assert t["id"] and t["default_duration_minutes"] == 90
        start = _future()
        m = svc.create_meeting(p, subject="From template", template_code=code, person_id=ids["pid"],
                               starts_at=start, actor_user_id=ids["uid"])
        assert m["meeting_type"] == "annual_review"
        assert m["location_type"] == "in_person"
        assert m["agenda"] == ["Portfolio", "Goals"]
        assert m["ends_at"] == start + timedelta(minutes=90)   # duration applied
        with pytest.raises(tmpl.TemplateError):
            tmpl.create_template(code=code, name="dup")        # duplicate code
    finally:
        _teardown(ids)


def test_seeded_meeting_templates_exist():
    for code in ("prospect_meeting", "discovery", "annual_review", "compliance_review"):
        assert tmpl.get_template(code=code) is not None


# --- resources + availability ------------------------------------------------

def test_resource_booking_and_conflict():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        res = tmpl.create_resource(code=f"r-{ids['tag']}", name="Board Room", resource_type="room",
                                   capacity=8)
        start = _future()
        m1 = svc.create_meeting(p, subject="M1", person_id=ids["pid"], starts_at=start,
                                ends_at=start + timedelta(hours=1), actor_user_id=ids["uid"])
        svc.book_resource(p, m1["id"], res["id"], actor_user_id=ids["uid"])
        m2 = svc.create_meeting(p, subject="M2", person_id=ids["pid"],
                                starts_at=start + timedelta(minutes=30),
                                ends_at=start + timedelta(minutes=90), actor_user_id=ids["uid"])
        with pytest.raises(svc.SchedulingError):
            svc.book_resource(p, m2["id"], res["id"], actor_user_id=ids["uid"])  # double-booked
    finally:
        _teardown(ids)


def test_availability_reports_busy_intervals():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        start = _future()
        svc.create_meeting(p, subject="Busy", person_id=ids["pid"], starts_at=start,
                           ends_at=start + timedelta(hours=1), actor_user_id=ids["uid"])
        window = avail.availability(start=start - timedelta(hours=1),
                                    end=start + timedelta(hours=2), person_id=ids["pid"],
                                    include_microsoft=False)
        assert window["busy_count"] >= 1 and window["free"] is False
        free = avail.availability(start=start + timedelta(days=30),
                                  end=start + timedelta(days=30, hours=1), person_id=ids["pid"],
                                  include_microsoft=False)
        assert free["free"] is True
        # M365 overlay path is reused (best-effort) and must not error.
        assert isinstance(avail.microsoft_busy_intervals(ids["pid"], start=start, end=start), list)
    finally:
        _teardown(ids)


# --- lifecycle ---------------------------------------------------------------

def test_lifecycle_transitions_and_invalid():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        m = svc.create_meeting(p, subject="LC", person_id=ids["pid"], starts_at=_future(),
                               actor_user_id=ids["uid"])
        m = svc.transition(p, m["id"], "confirmed", actor_user_id=ids["uid"])
        assert m["status"] == "confirmed"
        m = svc.transition(p, m["id"], "checked_in", actor_user_id=ids["uid"])
        m = svc.transition(p, m["id"], "completed", actor_user_id=ids["uid"])
        assert m["status"] == "completed"
        with pytest.raises(svc.SchedulingError):
            svc.transition(p, m["id"], "scheduled", actor_user_id=ids["uid"])  # completed terminal
    finally:
        _teardown(ids)


def test_reschedule_preserves_duration_and_publishes():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        start = _future()
        m = svc.create_meeting(p, subject="RS", person_id=ids["pid"], starts_at=start,
                               ends_at=start + timedelta(hours=1), actor_user_id=ids["uid"])
        new_start = start + timedelta(days=1)
        m = svc.reschedule(p, m["id"], starts_at=new_start, actor_user_id=ids["uid"])
        assert m["status"] == "scheduled"
        assert m["starts_at"] == new_start
        assert m["ends_at"] == new_start + timedelta(hours=1)   # duration preserved
    finally:
        _teardown(ids)


def test_record_outcome_completes():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        m = svc.create_meeting(p, subject="OC", person_id=ids["pid"], starts_at=_future(),
                               actor_user_id=ids["uid"])
        m = svc.record_outcome(p, m["id"], outcome="Great", outcome_notes="notes",
                               actor_user_id=ids["uid"])
        assert m["status"] == "completed" and m["outcome"] == "Great"
        assert m["outcome_recorded_at"] is not None
    finally:
        _teardown(ids)


# --- attendees + follow-ups + reminders --------------------------------------

def test_attendees_and_responses():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        m = svc.create_meeting(p, subject="AT", person_id=ids["pid"], starts_at=_future(),
                               actor_user_id=ids["uid"])
        a = svc.add_attendee(p, m["id"], attendee_ref="client@e.test", attendee_type="external",
                             attendee_role="required", actor_user_id=ids["uid"])
        assert a["response_status"] == "needs_action"
        a = svc.set_attendee_response(p, a["id"], "accepted", checked_in=True,
                                      actor_user_id=ids["uid"])
        assert a["response_status"] == "accepted" and a["checked_in_at"] is not None
        assert len(svc.get_meeting(p, m["id"])["attendees"]) == 1
    finally:
        _teardown(ids)


def test_followups_and_reminders():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        m = svc.create_meeting(p, subject="FU", person_id=ids["pid"], starts_at=_future(),
                               actor_user_id=ids["uid"])
        fu = svc.add_followup(p, m["id"], description="Send proposal", actor_user_id=ids["uid"])
        assert fu["status"] == "open"
        fu = svc.complete_followup(p, fu["id"], actor_user_id=ids["uid"])
        assert fu["status"] == "done"
        # reminder records intent in the reused notification ledger (metadata only)
        r = svc.add_reminder(p, m["id"], minutes_before=60, actor_user_id=ids["uid"])
        assert r["status"] == "scheduled" and r["notification_uid"]
    finally:
        _teardown(ids)


# --- authorization + record scope --------------------------------------------

def test_scope_blocks_stranger():
    ids = _setup()
    try:
        owner = _principal(ids["uid"])
        m = svc.create_meeting(owner, subject="Private", person_id=ids["pid"], starts_at=_future(),
                               actor_user_id=ids["uid"])
        stranger = _principal(ids["stranger"], {"scheduling.view", "scheduling.manage"})
        assert svc.get_meeting(stranger, m["id"]) is None
        assert all(r["id"] != m["id"] for r in svc.list_meetings(stranger)["rows"])
        with pytest.raises(svc.MeetingNotFound):
            svc.audit_history(stranger, m["id"])
    finally:
        _teardown(ids)


def test_scoped_owner_sees_and_org_scope():
    ids = _setup(with_org=True)
    try:
        owner = _principal(ids["uid"], {"scheduling.view", "scheduling.manage"})
        person_mtg = svc.create_meeting(owner, subject="Scoped", person_id=ids["pid"],
                                        starts_at=_future(), actor_user_id=ids["uid"])
        org_mtg = svc.create_meeting(owner, subject="Org", organization_id=ids["org_id"],
                                     starts_at=_future(), actor_user_id=ids["uid"])
        assert svc.get_meeting(owner, person_mtg["id"]) is not None
        assert svc.get_meeting(owner, org_mtg["id"]) is not None
        stranger = _principal(ids["stranger"], {"scheduling.view"})
        assert svc.get_meeting(stranger, org_mtg["id"]) is None
    finally:
        _teardown(ids)


def test_create_requires_write_scope():
    ids = _setup()
    try:
        stranger = _principal(ids["stranger"], {"scheduling.manage"})
        with pytest.raises(svc.SchedulingError):
            svc.create_meeting(stranger, subject="X", person_id=ids["pid"], starts_at=_future(),
                               actor_user_id=ids["stranger"])
    finally:
        _teardown(ids)


# --- cross-domain references -------------------------------------------------

def test_communications_reference():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        from app.services.communications import service as comms
        conv = comms.create_conversation(p, subject="Re: meeting", person_id=ids["pid"],
                                         actor_user_id=ids["uid"])
        m = svc.create_meeting(p, subject="Linked", person_id=ids["pid"], starts_at=_future(),
                               conversation_id=conv["id"], actor_user_id=ids["uid"])
        assert m["conversation_id"] == conv["id"]
    finally:
        _teardown(ids)


def test_cross_domain_fk_targets():
    """Scheduling references business domains via FK; it owns none of them."""
    def _target(col):
        return next(iter(meetings.c[col].foreign_keys)).column.table.name
    assert _target("opportunity_id") == "opportunities"
    assert _target("annual_review_session_id") == "annual_review_sessions"
    assert _target("conversation_id") == "communication_conversations"
    assert _target("workflow_instance_id") == "workflow_instances"
    assert _target("agenda_document_id") == "documents"
    assert _target("organization_id") == "relationship_entities"
    from app.db import meeting_followups
    assert next(iter(meeting_followups.c["advisor_work_item_id"].foreign_keys)).column.table.name \
        == "advisor_work_items"


# --- timeline integration ----------------------------------------------------

def test_timeline_lifecycle_events():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        m = svc.create_meeting(p, subject="TL", person_id=ids["pid"], starts_at=_future(),
                               actor_user_id=ids["uid"])
        svc.reschedule(p, m["id"], starts_at=_future(48), actor_user_id=ids["uid"])
        svc.record_outcome(p, m["id"], outcome="done", actor_user_id=ids["uid"])
        m2 = svc.create_meeting(p, subject="TL2", person_id=ids["pid"], starts_at=_future(),
                                actor_user_id=ids["uid"])
        svc.transition(p, m2["id"], "cancelled", actor_user_id=ids["uid"])
        with engine.connect() as c:
            types = set(c.scalars(select(timeline_events.c.event_type).where(
                timeline_events.c.source == "scheduling",
                timeline_events.c.person_id == ids["pid"])))
        assert "scheduling_meeting_scheduled" in types
        assert "scheduling_meeting_rescheduled" in types
        assert "scheduling_meeting_completed" in types
        assert "scheduling_meeting_cancelled" in types
    finally:
        _teardown(ids)


# --- analytics consumption ---------------------------------------------------

def test_analytics_upcoming_meetings_metric():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        before = sources.upcoming_meeting_count(p)
        svc.create_meeting(p, subject="Upcoming", person_id=ids["pid"], starts_at=_future(),
                           actor_user_id=ids["uid"])
        assert sources.upcoming_meeting_count(p) == before + 1
        from app.services.analytics.metrics import METRICS
        assert "upcoming_meetings" in METRICS
    finally:
        _teardown(ids)


# --- audit ledger ------------------------------------------------------------

def test_audit_ledger_records_and_is_append_only():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        m = svc.create_meeting(p, subject="Audit", person_id=ids["pid"], starts_at=_future(),
                               actor_user_id=ids["uid"])
        svc.transition(p, m["id"], "confirmed", actor_user_id=ids["uid"])
        etypes = [e["event_type"] for e in svc.audit_history(p, m["id"])]
        assert "meeting_created" in etypes and "meeting_confirmed" in etypes
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(update(scheduling_events).where(scheduling_events.c.meeting_id == m["id"])
                          .values(event_type="tampered"))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(delete(scheduling_events).where(scheduling_events.c.meeting_id == m["id"]))
    finally:
        _teardown(ids)


# --- architecture invariants -------------------------------------------------

def test_scheduling_does_not_import_analytics():
    import pathlib
    root = pathlib.Path(svc.__file__).parent
    for name in ("service.py", "availability.py", "templates.py"):
        src = (root / name).read_text()
        assert "import analytics" not in src and "services.analytics" not in src, name


def test_route_prefix_matches_no_middleware_rule():
    from app.security.middleware import RULES
    assert not any(pattern.search("/scheduling") for pattern, _cap in RULES)
    assert not any(pattern.search("/scheduling/5") for pattern, _cap in RULES)
