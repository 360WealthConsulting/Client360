"""Deterministic availability metadata (Phase D.19).

Availability is **deterministic and metadata only** — it composes busy windows from (a) scheduling
meetings (owned), (b) resource/room bookings (owned), and (c) the EXISTING Microsoft 365 calendar
sync via ``app/services/calendar.get_person_calendar_events`` (reused, never duplicated). There is
**no scheduling optimization and no AI recommendation** — it reports free/busy and conflicts by
plain interval overlap. A missing ``starts_at``/``ends_at`` simply cannot conflict.
"""
from __future__ import annotations

from sqlalchemy import and_, or_, select

from app.db import engine, meetings
from app.db import meeting_resource_bookings as bookings

# Meeting statuses that do NOT occupy a time slot.
_FREE_STATUSES = ("cancelled", "no_show", "rescheduled", "draft")


def _overlaps(col_start, col_end, start, end):
    """SQL predicate: [col_start, col_end) overlaps [start, end). Null bounds never conflict."""
    return and_(col_start.is_not(None), col_end.is_not(None), col_start < end, col_end > start)


def meeting_busy_intervals(*, start, end, person_id=None, household_id=None,
                           organizer_user_id=None, exclude_meeting_id=None) -> list[dict]:
    conds = [_overlaps(meetings.c.starts_at, meetings.c.ends_at, start, end),
             meetings.c.status.notin_(_FREE_STATUSES)]
    anchor = []
    if person_id is not None:
        anchor.append(meetings.c.person_id == person_id)
    if household_id is not None:
        anchor.append(meetings.c.household_id == household_id)
    if organizer_user_id is not None:
        anchor.append(meetings.c.organizer_user_id == organizer_user_id)
    if anchor:
        conds.append(or_(*anchor))
    if exclude_meeting_id is not None:
        conds.append(meetings.c.id != exclude_meeting_id)
    with engine.connect() as c:
        rows = c.execute(select(meetings.c.id, meetings.c.subject, meetings.c.starts_at,
                                meetings.c.ends_at, meetings.c.status)
                         .where(and_(*conds)).order_by(meetings.c.starts_at)).mappings()
        return [{"source": "meeting", "meeting_id": r["id"], "subject": r["subject"],
                 "starts_at": r["starts_at"], "ends_at": r["ends_at"]} for r in rows]


def resource_busy_intervals(resource_id: int, *, start, end, exclude_meeting_id=None) -> list[dict]:
    conds = [bookings.c.resource_id == resource_id, bookings.c.status == "booked",
             _overlaps(bookings.c.starts_at, bookings.c.ends_at, start, end)]
    if exclude_meeting_id is not None:
        conds.append(bookings.c.meeting_id != exclude_meeting_id)
    with engine.connect() as c:
        rows = c.execute(select(bookings.c.meeting_id, bookings.c.starts_at, bookings.c.ends_at)
                         .where(and_(*conds)).order_by(bookings.c.starts_at)).mappings()
        return [{"source": "resource", "meeting_id": r["meeting_id"], "starts_at": r["starts_at"],
                 "ends_at": r["ends_at"]} for r in rows]


def microsoft_busy_intervals(person_id: int, *, start, end) -> list[dict]:
    """Reuse the EXISTING Microsoft 365 calendar sync (timeline ``calendar_event`` rows). Never
    duplicates provider functionality — read-only, best-effort."""
    out: list[dict] = []
    try:
        from app.services.calendar import get_person_calendar_events
        for ev in get_person_calendar_events(person_id, limit=200):
            meta = ev.get("event_metadata") or {}
            s = ev.get("event_time") or meta.get("starts_at")
            e = meta.get("ends_at")
            if s is not None and e is not None and s < end and e > start:
                out.append({"source": "microsoft365", "subject": ev.get("title"),
                            "starts_at": s, "ends_at": e})
    except Exception:
        # M365 availability is an optional overlay; its absence never breaks scheduling.
        return out
    return out


def availability(*, start, end, person_id=None, household_id=None, organizer_user_id=None,
                 include_microsoft=True, exclude_meeting_id=None) -> dict:
    """Deterministic free/busy metadata for a window. Returns busy intervals from meetings,
    resource bookings are queried separately; M365 events are an optional overlay."""
    busy = meeting_busy_intervals(start=start, end=end, person_id=person_id,
                                  household_id=household_id, organizer_user_id=organizer_user_id,
                                  exclude_meeting_id=exclude_meeting_id)
    if include_microsoft and person_id is not None:
        busy += microsoft_busy_intervals(person_id, start=start, end=end)
    busy.sort(key=lambda b: b["starts_at"])
    return {"start": start, "end": end, "busy": busy, "busy_count": len(busy),
            "free": len(busy) == 0}


def slot_conflicts(*, start, end, person_id=None, organizer_user_id=None, resource_id=None,
                   exclude_meeting_id=None) -> list[dict]:
    """Deterministic conflict list for a proposed slot (empty == free)."""
    conflicts = meeting_busy_intervals(start=start, end=end, person_id=person_id,
                                       organizer_user_id=organizer_user_id,
                                       exclude_meeting_id=exclude_meeting_id)
    if resource_id is not None:
        conflicts += resource_busy_intervals(resource_id, start=start, end=end,
                                             exclude_meeting_id=exclude_meeting_id)
    return conflicts
