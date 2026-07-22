"""Scheduling service (Phase D.19) — authoritative for scheduling metadata.

Owns meetings/appointments, attendees, resource bookings, reminders, follow-ups, and the
append-only audit ledger. It **references** business entities (people/households/organizations,
opportunities, annual reviews, communications conversations, workflow instances, advisor-work
items, documents, Microsoft 365 events) and never becomes their source of truth. Record scope is
always enforced: a meeting is visible via its person/household anchor (or ``record.read_all``), its
organization anchor (``organization_in_scope``), or — for internal/firm meetings with no anchor —
to ``scheduling.view`` holders. Reminders reuse the notification ledger (metadata only) and
approved lifecycle events flow to the shared Activity Timeline.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select

from app.database.scheduling_tables import (
    ATTENDEE_ROLES,
    ATTENDEE_TYPES,
    LOCATION_TYPES,
    MEETING_CATEGORIES,
    MEETING_PRIORITIES,
    MEETING_STATUSES,
    MEETING_TYPES,
)
from app.db import engine, meetings, people, record_assignments
from app.db import meeting_attendees as attendees
from app.db import meeting_followups as followups
from app.db import meeting_reminders as reminders
from app.db import meeting_resource_bookings as bookings
from app.db import scheduling_events as events
from app.security.authorization import (
    accessible_person_ids,
    organization_in_scope,
    record_in_scope,
    team_ids,
)
from app.services.scheduling import templates as tmpl

# Deterministic lifecycle state machine.
_TRANSITIONS = {
    "draft": {"scheduled", "cancelled"},
    "scheduled": {"confirmed", "checked_in", "completed", "cancelled", "no_show", "rescheduled"},
    "confirmed": {"checked_in", "completed", "cancelled", "no_show", "rescheduled"},
    "checked_in": {"completed", "cancelled", "no_show"},
    "completed": set(),
    "cancelled": set(),
    "no_show": set(),
    "rescheduled": {"scheduled"},
}
# Statuses that publish an APPROVED lifecycle event to the shared timeline.
_TIMELINE_STATUSES = {"scheduled": "scheduling_meeting_scheduled",
                      "completed": "scheduling_meeting_completed",
                      "cancelled": "scheduling_meeting_cancelled"}


class SchedulingError(Exception):
    """Validation or lifecycle error."""


class MeetingNotFound(Exception):
    """Meeting not found or out of scope."""


def _now():
    return datetime.now(UTC)


def _json(payload):
    return json.loads(json.dumps(payload or {}, default=str))


# --- scope -------------------------------------------------------------------

def _accessible_org_ids(c, principal) -> set[int]:
    tids = team_ids(c, principal)
    conds = [record_assignments.c.user_id == principal.user_id]
    if tids:
        conds.append(record_assignments.c.team_id.in_(tuple(tids)))
    rows = c.scalars(select(record_assignments.c.entity_id).where(
        record_assignments.c.entity_type == "organization", or_(*conds)))
    return {r for r in rows if r is not None}


def _scope_clause(principal, c):
    if principal.can("record.read_all"):
        return None
    conds = [and_(meetings.c.person_id.is_(None), meetings.c.household_id.is_(None),
                  meetings.c.organization_id.is_(None))]        # internal/firm meetings
    ids = accessible_person_ids(c, principal)
    if ids:
        conds.append(meetings.c.person_id.in_(tuple(ids)))
        hh = set(c.scalars(select(people.c.household_id).where(
            people.c.id.in_(tuple(ids)), people.c.household_id.is_not(None))))
        if hh:
            conds.append(meetings.c.household_id.in_(tuple(hh)))
    orgs = _accessible_org_ids(c, principal)
    if orgs:
        conds.append(meetings.c.organization_id.in_(tuple(orgs)))
    return or_(*conds)


def _visible(principal, m: dict, c) -> bool:
    if principal.can("record.read_all"):
        return True
    if m.get("person_id") and record_in_scope(principal, "person", m["person_id"], connection=c):
        return True
    if m.get("household_id") and record_in_scope(principal, "household", m["household_id"], connection=c):
        return True
    if m.get("organization_id") and organization_in_scope(principal, m["organization_id"], connection=c):
        return True
    return not (m.get("person_id") or m.get("household_id") or m.get("organization_id"))


def _can_write(principal, m: dict, c) -> bool:
    if principal.can("record.write_all") or principal.can("record.read_all"):
        return True
    if m.get("person_id") and record_in_scope(principal, "person", m["person_id"], write=True, connection=c):
        return True
    if m.get("household_id") and record_in_scope(principal, "household", m["household_id"], write=True, connection=c):
        return True
    if m.get("organization_id") and organization_in_scope(principal, m["organization_id"], write=True, connection=c):
        return True
    return not (m.get("person_id") or m.get("household_id") or m.get("organization_id"))


def _load_scoped(c, principal, meeting_id: int, *, write=False) -> dict:
    m = c.execute(select(meetings).where(meetings.c.id == meeting_id)).mappings().first()
    if m is None or not _visible(principal, dict(m), c):
        raise MeetingNotFound(str(meeting_id))
    m = dict(m)
    if write and not _can_write(principal, m, c):
        raise SchedulingError("write not permitted in record scope")
    return m


# --- reads -------------------------------------------------------------------

def list_meetings(principal, *, status=None, meeting_type=None, category=None, search=None,
                  upcoming_only=False, page=1, page_size=50) -> dict:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    with engine.connect() as c:
        scope = _scope_clause(principal, c)
        conds = []
        if scope is not None:
            conds.append(scope)
        if status:
            conds.append(meetings.c.status == status)
        if meeting_type:
            conds.append(meetings.c.meeting_type == meeting_type)
        if category:
            conds.append(meetings.c.category == category)
        if search:
            conds.append(meetings.c.subject.ilike(f"%{search.strip()}%"))
        if upcoming_only:
            conds.append(and_(meetings.c.starts_at.is_not(None), meetings.c.starts_at >= _now(),
                              meetings.c.status.in_(("scheduled", "confirmed"))))
        where = and_(*conds) if conds else None
        base = select(func.count()).select_from(meetings)
        total = c.scalar(base.where(where) if where is not None else base)
        stmt = select(meetings)
        if where is not None:
            stmt = stmt.where(where)
        order = (meetings.c.starts_at.asc() if upcoming_only
                 else func.coalesce(meetings.c.starts_at, meetings.c.created_at).desc())
        rows = [dict(r) for r in c.execute(
            stmt.order_by(order, meetings.c.id.desc())
            .limit(page_size).offset((page - 1) * page_size)).mappings()]
    return {"rows": rows, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size if total else 0}


def get_meeting(principal, meeting_id: int) -> dict | None:
    with engine.connect() as c:
        try:
            m = _load_scoped(c, principal, meeting_id)
        except (MeetingNotFound, SchedulingError):
            return None
        m["attendees"] = [dict(r) for r in c.execute(
            select(attendees).where(attendees.c.meeting_id == meeting_id)
            .order_by(attendees.c.id)).mappings()]
        m["bookings"] = [dict(r) for r in c.execute(
            select(bookings).where(bookings.c.meeting_id == meeting_id)
            .order_by(bookings.c.id)).mappings()]
        m["reminders"] = [dict(r) for r in c.execute(
            select(reminders).where(reminders.c.meeting_id == meeting_id)
            .order_by(reminders.c.id)).mappings()]
        m["followups"] = [dict(r) for r in c.execute(
            select(followups).where(followups.c.meeting_id == meeting_id)
            .order_by(followups.c.id)).mappings()]
    return m


# --- timeline ----------------------------------------------------------------

def _publish_timeline(m: dict, status: str):
    event_type = _TIMELINE_STATUSES.get(status)
    if event_type is None:
        return
    if not m.get("person_id") and not m.get("household_id"):
        return
    try:
        from app.services.timeline import add_timeline_event
        add_timeline_event(
            source="scheduling", event_type=event_type, title=m.get("subject") or "Meeting",
            summary=(m.get("meeting_type") or ""), person_id=m.get("person_id"),
            household_id=m.get("household_id"), event_time=m.get("starts_at"),
            external_id=f"scheduling-{m['id']}-{status}",
            event_metadata={"meeting_id": m["id"], "status": status,
                            "meeting_type": m.get("meeting_type")})
    except Exception:
        pass


# --- meetings ----------------------------------------------------------------

def create_meeting(principal, *, subject, meeting_type="general", category="general",
                   status="scheduled", priority="normal", person_id=None, household_id=None,
                   organization_id=None, template_code=None, starts_at=None, ends_at=None,
                   timezone="America/Chicago", location=None, location_type="virtual",
                   virtual_url=None, agenda=None, preparation_checklist=None, recurrence=None,
                   opportunity_id=None, annual_review_session_id=None, conversation_id=None,
                   workflow_instance_id=None, agenda_document_id=None, microsoft_event_id=None,
                   actor_user_id=None) -> dict:
    subject = (subject or "").strip()
    if not subject:
        raise SchedulingError("subject is required")
    if meeting_type not in MEETING_TYPES:
        raise SchedulingError(f"invalid meeting_type {meeting_type!r}")
    if category not in MEETING_CATEGORIES:
        raise SchedulingError(f"invalid category {category!r}")
    if priority not in MEETING_PRIORITIES:
        raise SchedulingError(f"invalid priority {priority!r}")
    if location_type not in LOCATION_TYPES:
        raise SchedulingError(f"invalid location_type {location_type!r}")
    if status not in ("draft", "scheduled"):
        raise SchedulingError("new meetings start as 'draft' or 'scheduled'")
    if person_id is not None and not record_in_scope(principal, "person", person_id, write=True):
        raise SchedulingError("person not in write scope")
    if household_id is not None and not record_in_scope(principal, "household", household_id, write=True):
        raise SchedulingError("household not in write scope")
    if organization_id is not None and not organization_in_scope(principal, organization_id, write=True):
        raise SchedulingError("organization not in write scope")

    # Apply a template's deterministic defaults (agenda, checklist, duration, location type).
    template_id = None
    if template_code:
        t = tmpl.get_template(code=template_code)
        if t is None or not t.get("active"):
            raise SchedulingError(f"unknown or inactive template {template_code!r}")
        template_id = t["id"]
        meeting_type = meeting_type if meeting_type != "general" else t["meeting_type"]
        category = category if category != "general" else t["category"]
        location_type = location_type if location_type != "virtual" else t["default_location_type"]
        agenda = agenda if agenda is not None else t.get("agenda")
        preparation_checklist = (preparation_checklist if preparation_checklist is not None
                                 else t.get("preparation_checklist"))
        if starts_at is not None and ends_at is None and t.get("default_duration_minutes"):
            ends_at = starts_at + timedelta(minutes=int(t["default_duration_minutes"]))
    if starts_at is not None and ends_at is not None and ends_at <= starts_at:
        raise SchedulingError("ends_at must be after starts_at")

    now = _now()
    with engine.begin() as c:
        m = c.execute(meetings.insert().values(
            subject=subject, meeting_type=meeting_type, category=category, status=status,
            priority=priority, organizer_user_id=actor_user_id, person_id=person_id,
            household_id=household_id, organization_id=organization_id, template_id=template_id,
            opportunity_id=opportunity_id, annual_review_session_id=annual_review_session_id,
            conversation_id=conversation_id, workflow_instance_id=workflow_instance_id,
            agenda_document_id=agenda_document_id, microsoft_event_id=microsoft_event_id,
            starts_at=starts_at, ends_at=ends_at, timezone=timezone, location=location,
            location_type=location_type, virtual_url=virtual_url, agenda=agenda,
            preparation_checklist=preparation_checklist, recurrence=recurrence,
            last_status_at=now, created_by_user_id=actor_user_id, created_at=now,
            updated_at=now).returning(*meetings.c)).mappings().one()
        m = dict(m)
        c.execute(events.insert().values(
            meeting_id=m["id"], event_type="meeting_created", to_status=status,
            actor_user_id=actor_user_id, payload=_json({"meeting_type": meeting_type}),
            occurred_at=now))
    _publish_timeline(m, status)
    return m


def update_meeting(principal, meeting_id: int, *, actor_user_id=None, **fields) -> dict:
    allowed = {"subject", "priority", "location", "location_type", "virtual_url", "agenda",
               "preparation_checklist", "recurrence", "microsoft_event_id", "opportunity_id",
               "annual_review_session_id", "conversation_id", "workflow_instance_id",
               "agenda_document_id", "meeting_metadata", "tags", "timezone"}
    values = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "location_type" in values and values["location_type"] not in LOCATION_TYPES:
        raise SchedulingError("invalid location_type")
    if not values:
        raise SchedulingError("no updatable fields provided")
    with engine.begin() as c:
        _load_scoped(c, principal, meeting_id, write=True)
        values["updated_at"] = _now()
        m = c.execute(meetings.update().where(meetings.c.id == meeting_id)
                      .values(**values).returning(*meetings.c)).mappings().one()
        c.execute(events.insert().values(
            meeting_id=meeting_id, event_type="meeting_updated", actor_user_id=actor_user_id,
            payload=_json({"fields": sorted(values.keys())}), occurred_at=values["updated_at"]))
        return dict(m)


def transition(principal, meeting_id: int, status: str, *, actor_user_id=None, reason=None) -> dict:
    if status not in MEETING_STATUSES:
        raise SchedulingError(f"invalid status {status!r}")
    with engine.begin() as c:
        m = _load_scoped(c, principal, meeting_id, write=True)
        current = m["status"]
        if status != current and status not in _TRANSITIONS.get(current, set()):
            raise SchedulingError(f"cannot transition {current!r} -> {status!r}")
        now = _now()
        updated = c.execute(meetings.update().where(meetings.c.id == meeting_id)
                            .values(status=status, last_status_at=now, updated_at=now)
                            .returning(*meetings.c)).mappings().one()
        c.execute(events.insert().values(
            meeting_id=meeting_id, event_type=f"meeting_{status}", from_status=current,
            to_status=status, actor_user_id=actor_user_id, payload=_json({"reason": reason}),
            occurred_at=now))
        updated = dict(updated)
    _publish_timeline(updated, status)
    return updated


def reschedule(principal, meeting_id: int, *, starts_at, ends_at=None, actor_user_id=None,
               reason=None) -> dict:
    if starts_at is None:
        raise SchedulingError("starts_at is required to reschedule")
    if ends_at is not None and ends_at <= starts_at:
        raise SchedulingError("ends_at must be after starts_at")
    with engine.begin() as c:
        m = _load_scoped(c, principal, meeting_id, write=True)
        if m["status"] in ("completed", "cancelled", "no_show"):
            raise SchedulingError(f"cannot reschedule a {m['status']} meeting")
        now = _now()
        new_end = ends_at
        if new_end is None and m["starts_at"] is not None and m["ends_at"] is not None:
            new_end = starts_at + (m["ends_at"] - m["starts_at"])   # preserve duration
        updated = c.execute(meetings.update().where(meetings.c.id == meeting_id).values(
            starts_at=starts_at, ends_at=new_end, status="scheduled", last_status_at=now,
            updated_at=now).returning(*meetings.c)).mappings().one()
        c.execute(events.insert().values(
            meeting_id=meeting_id, event_type="meeting_rescheduled", from_status=m["status"],
            to_status="scheduled", actor_user_id=actor_user_id,
            payload=_json({"from": m["starts_at"], "to": starts_at, "reason": reason}),
            occurred_at=now))
        updated = dict(updated)
    # reschedule is an approved lifecycle event.
    _publish_lifecycle(updated, "scheduling_meeting_rescheduled")
    return updated


def _publish_lifecycle(m: dict, event_type: str):
    if not m.get("person_id") and not m.get("household_id"):
        return
    try:
        from app.services.timeline import add_timeline_event
        add_timeline_event(
            source="scheduling", event_type=event_type, title=m.get("subject") or "Meeting",
            summary=(m.get("meeting_type") or ""), person_id=m.get("person_id"),
            household_id=m.get("household_id"), event_time=m.get("starts_at"),
            external_id=f"scheduling-{m['id']}-{event_type}-{int(m['updated_at'].timestamp())}",
            event_metadata={"meeting_id": m["id"]})
    except Exception:
        pass


def record_outcome(principal, meeting_id: int, *, outcome=None, outcome_notes=None,
                   complete=True, actor_user_id=None) -> dict:
    """Record a meeting outcome (scheduling metadata). Optionally transitions to completed and
    publishes the approved ``scheduling_meeting_completed`` timeline event."""
    with engine.begin() as c:
        m = _load_scoped(c, principal, meeting_id, write=True)
        now = _now()
        values = {"outcome": outcome, "outcome_notes": outcome_notes, "outcome_recorded_at": now,
                  "updated_at": now}
        new_status = m["status"]
        if complete and m["status"] not in ("cancelled", "no_show"):
            new_status = "completed"
            values["status"] = "completed"
            values["last_status_at"] = now
        updated = c.execute(meetings.update().where(meetings.c.id == meeting_id)
                            .values(**values).returning(*meetings.c)).mappings().one()
        c.execute(events.insert().values(
            meeting_id=meeting_id, event_type="meeting_outcome_recorded", to_status=new_status,
            actor_user_id=actor_user_id, payload=_json({"has_notes": bool(outcome_notes)}),
            occurred_at=now))
        updated = dict(updated)
    if updated["status"] == "completed":
        _publish_timeline(updated, "completed")
    return updated


# --- attendees / bookings / reminders / follow-ups ---------------------------

def add_attendee(principal, meeting_id: int, *, attendee_ref, attendee_type="person",
                 attendee_role="required", display_name=None, actor_user_id=None) -> dict:
    if attendee_type not in ATTENDEE_TYPES:
        raise SchedulingError(f"invalid attendee_type {attendee_type!r}")
    if attendee_role not in ATTENDEE_ROLES:
        raise SchedulingError(f"invalid attendee_role {attendee_role!r}")
    ref = str(attendee_ref or "").strip()
    if not ref:
        raise SchedulingError("attendee_ref is required")
    with engine.begin() as c:
        _load_scoped(c, principal, meeting_id, write=True)
        row = c.execute(attendees.insert().values(
            meeting_id=meeting_id, attendee_type=attendee_type, attendee_ref=ref,
            attendee_role=attendee_role, display_name=display_name,
            response_status="needs_action").returning(*attendees.c)).mappings().one()
        c.execute(events.insert().values(
            meeting_id=meeting_id, event_type="attendee_added", actor_user_id=actor_user_id,
            payload=_json({"attendee_ref": ref, "role": attendee_role}), occurred_at=_now()))
        return dict(row)


def set_attendee_response(principal, attendee_id: int, response_status: str, *,
                          checked_in=False, actor_user_id=None) -> dict:
    from app.database.scheduling_tables import RESPONSE_STATUSES
    if response_status not in RESPONSE_STATUSES:
        raise SchedulingError(f"invalid response_status {response_status!r}")
    with engine.begin() as c:
        att = c.execute(select(attendees).where(attendees.c.id == attendee_id)).mappings().first()
        if att is None:
            raise MeetingNotFound(f"attendee {attendee_id}")
        _load_scoped(c, principal, att["meeting_id"], write=True)
        values = {"response_status": response_status}
        if checked_in:
            values["checked_in_at"] = _now()
        row = c.execute(attendees.update().where(attendees.c.id == attendee_id)
                        .values(**values).returning(*attendees.c)).mappings().one()
        return dict(row)


def book_resource(principal, meeting_id: int, resource_id: int, *, starts_at=None, ends_at=None,
                  actor_user_id=None) -> dict:
    with engine.begin() as c:
        m = _load_scoped(c, principal, meeting_id, write=True)
        s = starts_at or m["starts_at"]
        e = ends_at or m["ends_at"]
        # Deterministic conflict check (no optimization): block a double-booked resource.
        if s is not None and e is not None:
            from app.services.scheduling.availability import resource_busy_intervals
            if resource_busy_intervals(resource_id, start=s, end=e, exclude_meeting_id=meeting_id):
                raise SchedulingError("resource is already booked for that window")
        row = c.execute(bookings.insert().values(
            meeting_id=meeting_id, resource_id=resource_id, starts_at=s, ends_at=e,
            status="booked").returning(*bookings.c)).mappings().one()
        c.execute(events.insert().values(
            meeting_id=meeting_id, event_type="resource_booked", actor_user_id=actor_user_id,
            payload=_json({"resource_id": resource_id}), occurred_at=_now()))
        return dict(row)


def add_reminder(principal, meeting_id: int, *, minutes_before=None, remind_at=None,
                 channel="internal_notification", actor_user_id=None) -> dict:
    """Schedule a reminder (metadata only). Records intent in the reused notification ledger and
    links the ``notification_uid``; no dispatch is performed here."""
    with engine.begin() as c:
        m = _load_scoped(c, principal, meeting_id, write=True)
        eff_remind_at = remind_at
        if eff_remind_at is None and minutes_before is not None and m["starts_at"] is not None:
            eff_remind_at = m["starts_at"] - timedelta(minutes=int(minutes_before))
        notification_uid = None
        try:
            from app.services.notifications import record_notification
            rec = record_notification(
                notification_type="scheduling.reminder", recipient_type="user",
                recipient_ref=str(m.get("organizer_user_id") or actor_user_id or "firm"),
                channel=channel, title=f"Reminder: {m.get('subject') or 'Meeting'}",
                source_ref=f"meeting:{meeting_id}",
                metadata={"meeting_id": meeting_id}, conn=c)
            notification_uid = rec.notification_uid
        except Exception:
            notification_uid = None
        row = c.execute(reminders.insert().values(
            meeting_id=meeting_id, remind_at=eff_remind_at, minutes_before=minutes_before,
            channel=channel, status="scheduled",
            notification_uid=notification_uid).returning(*reminders.c)).mappings().one()
        c.execute(events.insert().values(
            meeting_id=meeting_id, event_type="reminder_scheduled", actor_user_id=actor_user_id,
            payload=_json({"minutes_before": minutes_before}), occurred_at=_now()))
        return dict(row)


def add_followup(principal, meeting_id: int, *, description, due_date=None, assigned_user_id=None,
                 advisor_work_item_id=None, actor_user_id=None) -> dict:
    description = (description or "").strip()
    if not description:
        raise SchedulingError("follow-up description is required")
    with engine.begin() as c:
        _load_scoped(c, principal, meeting_id, write=True)
        row = c.execute(followups.insert().values(
            meeting_id=meeting_id, description=description, due_date=due_date, status="open",
            assigned_user_id=assigned_user_id, advisor_work_item_id=advisor_work_item_id,
            created_by_user_id=actor_user_id).returning(*followups.c)).mappings().one()
        c.execute(events.insert().values(
            meeting_id=meeting_id, event_type="followup_added", actor_user_id=actor_user_id,
            payload=_json({"assigned_user_id": assigned_user_id}), occurred_at=_now()))
        return dict(row)


def complete_followup(principal, followup_id: int, *, status="done", actor_user_id=None) -> dict:
    from app.database.scheduling_tables import FOLLOWUP_STATUSES
    if status not in FOLLOWUP_STATUSES:
        raise SchedulingError(f"invalid follow-up status {status!r}")
    with engine.begin() as c:
        fu = c.execute(select(followups).where(followups.c.id == followup_id)).mappings().first()
        if fu is None:
            raise MeetingNotFound(f"followup {followup_id}")
        _load_scoped(c, principal, fu["meeting_id"], write=True)
        row = c.execute(followups.update().where(followups.c.id == followup_id)
                        .values(status=status, updated_at=_now()).returning(*followups.c)).mappings().one()
        return dict(row)


# --- audit + metrics ---------------------------------------------------------

def audit_history(principal, meeting_id: int) -> list[dict]:
    with engine.connect() as c:
        _load_scoped(c, principal, meeting_id)
        return [dict(e) for e in c.execute(
            select(events).where(events.c.meeting_id == meeting_id)
            .order_by(events.c.occurred_at, events.c.id)).mappings()]


def metrics(principal) -> dict:
    with engine.connect() as c:
        scope = _scope_clause(principal, c)
        def _count(*extra):
            stmt = select(func.count()).select_from(meetings)
            conds = [] if scope is None else [scope]
            conds.extend(extra)
            return c.scalar(stmt.where(and_(*conds)) if conds else stmt) or 0
        upcoming = _count(meetings.c.starts_at.is_not(None), meetings.c.starts_at >= _now(),
                          meetings.c.status.in_(("scheduled", "confirmed")))
        return {"total": _count(), "upcoming": upcoming,
                "completed": _count(meetings.c.status == "completed"),
                "cancelled": _count(meetings.c.status == "cancelled")}
