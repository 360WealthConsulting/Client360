"""Domain-events adapter (Phase D.10).

Projects the existing authoritative ``timeline_events`` stream — already populated by
tasks, notes, communications, imports, source links, matching, filings, etc. via
``add_timeline_event`` — into ``TimelineEvent``. These events are already advisor-facing,
so no redaction is applied; the adapter only reads (never writes) and never fabricates
history from ``updated_at``.
"""
from __future__ import annotations

from sqlalchemy import or_, select

from app.db import timeline_events
from app.services.activity_timeline.models import TimelineEvent

SOURCE_DOMAIN = "activity"


def events(conn, *, person_ids: tuple[int, ...], household_id: int | None, limit: int, redact: dict):
    conds = []
    if person_ids:
        conds.append(timeline_events.c.person_id.in_(person_ids))
    if household_id is not None:
        conds.append(timeline_events.c.household_id == household_id)
    if not conds:
        return []
    rows = conn.execute(
        select(timeline_events).where(or_(*conds))
        .order_by(timeline_events.c.event_time.desc(), timeline_events.c.id.desc())
        .limit(limit)
    ).mappings().all()
    out = []
    for r in rows:
        meta = r["event_metadata"] or {}
        actor = meta.get("actor_user_id") or meta.get("recorded_by_user_id")
        out.append(TimelineEvent(
            event_id=f"domain:timeline_event:{r['id']}",
            event_type=r["event_type"],
            occurred_at=r["event_time"],
            title=r["title"] or _fallback_title(r["event_type"]),
            summary=r["summary"] or "",
            person_id=r["person_id"], household_id=r["household_id"],
            source_domain=SOURCE_DOMAIN, source_record_type="timeline_event", source_record_id=r["id"],
            actor_principal_id=actor,
            source_url=(f"/people/{r['person_id']}" if r["person_id"] else None),
        ))
    return out


def _fallback_title(event_type: str) -> str:
    return (event_type or "activity").replace("_", " ").capitalize()
