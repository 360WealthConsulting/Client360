"""Advisor-work adapter (Phase D.10).

Projects the append-only ``advisor_work_events`` ledger (D.9), joined to
``advisor_work_items`` for the person/household link, into ``TimelineEvent``. The event's
existence is shown to any timeline-authorized principal; its **note** and the source link
are redacted unless the principal holds ``advisor_work.read`` (``redact['advisor_work']``).
Read-only; no duplicate current-status + transition events (one row per action).
"""
from __future__ import annotations

from sqlalchemy import or_, select

from app.db import advisor_work_events, advisor_work_items
from app.services.activity_timeline.models import TimelineEvent

SOURCE_DOMAIN = "advisor_work"

_TITLES = {
    "created": "Advisor work created",
    "assigned": "Advisor work assigned",
    "in_progress": "Advisor work started",
    "waiting": "Advisor work waiting",
    "completed": "Advisor work completed",
    "cancelled": "Advisor work cancelled",
    "archived": "Advisor work archived",
}


def events(conn, *, person_ids: tuple[int, ...], household_id: int | None, limit: int, redact: dict):
    can = bool(redact.get("advisor_work"))
    conds = []
    if person_ids:
        conds.append(advisor_work_items.c.person_id.in_(person_ids))
    if household_id is not None:
        conds.append(advisor_work_items.c.household_id == household_id)
    if not conds:
        return []
    j = advisor_work_events.join(
        advisor_work_items, advisor_work_items.c.id == advisor_work_events.c.advisor_work_item_id)
    rows = conn.execute(
        select(advisor_work_events, advisor_work_items.c.person_id.label("p_person"),
               advisor_work_items.c.household_id.label("p_household"))
        .select_from(j).where(or_(*conds))
        .order_by(advisor_work_events.c.occurred_at.desc(), advisor_work_events.c.id.desc())
        .limit(limit)
    ).mappings().all()
    out = []
    for r in rows:
        note = r["note"]
        redacted = bool(note) and not can
        out.append(TimelineEvent(
            event_id=f"advisor_work:event:{r['id']}",
            event_type=f"advisor_work.{r['event_type']}",
            occurred_at=r["occurred_at"],
            title=_TITLES.get(r["event_type"], "Advisor work updated"),
            summary=(note or "") if can else ("Additional details are restricted." if note else ""),
            person_id=r["p_person"], household_id=r["p_household"],
            source_domain=SOURCE_DOMAIN, source_record_type="advisor_work_item",
            source_record_id=r["advisor_work_item_id"],
            actor_principal_id=r["actor_principal_id"],
            status=r["new_status"],
            source_url=(f"/advisor-work/{r['advisor_work_item_id']}" if can else None),
            redacted=redacted,
        ))
    return out
