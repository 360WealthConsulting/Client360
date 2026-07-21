from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import engine, timeline_events

EVENT_DISPLAY = {
    "note_updated": {
        "icon": "📝",
        "label": "Advisor Note",
        "style": "note",
    },
    "activity_note_added": {
        "icon": "📝",
        "label": "Activity Note",
        "style": "note",
    },
    "communication_logged": {
        "icon": "💬",
        "label": "Communication",
        "style": "activity",
    },
    "person_updated": {
        "icon": "✏",
        "label": "Client Updated",
        "style": "note",
    },
    "task_created": {
        "icon": "＋",
        "label": "Task Created",
        "style": "task",
    },
    "task_completed": {
        "icon": "✓",
        "label": "Task Completed",
        "style": "task-complete",
    },
    "document_uploaded": {
        "icon": "📄",
        "label": "Document Uploaded",
        "style": "document",
    },
    "activity_created": {
        "icon": "📋",
        "label": "Activity",
        "style": "activity",
    },
    "email_received": {
        "icon": "✉",
        "label": "Email Received",
        "style": "email",
    },
    "calendar_event": {
        "icon": "📅",
        "label": "Calendar Event",
        "style": "calendar",
    },
    "microsoft_document": {
        "icon": "📄",
        "label": "Microsoft Document",
        "style": "document",
    },
    "relationship_added": {
        "icon": "🔗",
        "label": "Relationship Added",
        "style": "relationship",
    },
    "relationship_updated": {
        "icon": "🔗",
        "label": "Relationship Updated",
        "style": "relationship",
    },
    "assignment_created": {"icon": "👤", "label": "Assignment Added", "style": "work"},
    "assignment_changed": {"icon": "↻", "label": "Assignment Changed", "style": "work"},
    "assignment_removed": {"icon": "−", "label": "Assignment Removed", "style": "work"},
    "work_escalated": {"icon": "!", "label": "Work Escalated", "style": "work"},
    "sla_risk": {"icon": "⏱", "label": "SLA Risk", "style": "work"},
    "queue_changed": {"icon": "⇢", "label": "Queue Changed", "style": "work"},
    "portfolio_account_opened": {"icon": "💼", "label": "Account Opened", "style": "portfolio"},
    "portfolio_account_closed": {"icon": "💼", "label": "Account Closed", "style": "portfolio"},
    "portfolio_transfer": {"icon": "↔", "label": "Transfer Completed", "style": "portfolio"},
    "portfolio_billing_updated": {"icon": "$", "label": "Billing Updated", "style": "portfolio"},
    "portfolio_cash_movement": {"icon": "$", "label": "Large Cash Movement", "style": "portfolio"},
    "portfolio_allocation_changed": {"icon": "◔", "label": "Allocation Changed", "style": "portfolio"},
    "portfolio_beneficiary_updated": {"icon": "✓", "label": "Beneficiary Updated", "style": "portfolio"},
    "test": {
        "icon": "⚙",
        "label": "System Event",
        "style": "system",
    },
}


def _relative_time(value):
    if value is None:
        return "Date unavailable"

    now = datetime.now(timezone.utc)

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    delta = now - value
    seconds = max(int(delta.total_seconds()), 0)

    if seconds < 60:
        return "Just now"

    minutes = seconds // 60

    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"

    hours = minutes // 60

    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"

    days = hours // 24

    if days == 1:
        return "Yesterday"

    if days < 7:
        return f"{days} days ago"

    if value.year == now.year:
        return value.strftime("%b %-d")

    return value.strftime("%b %-d, %Y")


def _decorate_event(row):
    event = dict(row)
    display = EVENT_DISPLAY.get(
        event["event_type"],
        {
            "icon": "•",
            "label": event["event_type"].replace("_", " ").title(),
            "style": "default",
        },
    )

    event["display_icon"] = display["icon"]
    event["display_label"] = display["label"]
    event["display_style"] = display["style"]
    event["relative_time"] = _relative_time(
        event.get("event_time")
    )

    return event



def add_timeline_event(
    *,
    source: str,
    event_type: str,
    title: str,
    person_id: Optional[int] = None,
    household_id: Optional[int] = None,
    summary: Optional[str] = None,
    event_time: Optional[datetime] = None,
    external_id: Optional[str] = None,
    event_metadata: Optional[dict[str, Any]] = None,
) -> int:
    if person_id is None and household_id is None:
        raise ValueError(
            "A timeline event must have a person_id or household_id."
        )

    values = {
        "person_id": person_id,
        "household_id": household_id,
        "source": source,
        "event_type": event_type,
        "title": title,
        "summary": summary,
        "event_time": event_time or datetime.now(timezone.utc),
        "external_id": external_id,
        "event_metadata": event_metadata or {},
    }

    statement = pg_insert(timeline_events).values(**values)

    if external_id:
        statement = statement.on_conflict_do_update(
            constraint="uq_timeline_source_external_id",
            set_={
                "person_id": person_id,
                "household_id": household_id,
                "event_type": event_type,
                "title": title,
                "summary": summary,
                "event_time": values["event_time"],
                "event_metadata": values["event_metadata"],
                "updated_at": datetime.now(timezone.utc),
            },
        )

    statement = statement.returning(timeline_events.c.id)

    with engine.begin() as connection:
        return connection.execute(statement).scalar_one()


def get_person_timeline(
    person_id: int,
    limit: int = 100,
):
    statement = (
        select(timeline_events)
        .where(timeline_events.c.person_id == person_id)
        .order_by(
            timeline_events.c.event_time.desc(),
            timeline_events.c.id.desc(),
        )
        .limit(limit)
    )

    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()

    return [_decorate_event(row) for row in rows]


def get_household_timeline(
    household_id: int,
    limit: int = 100,
):
    statement = (
        select(timeline_events)
        .where(timeline_events.c.household_id == household_id)
        .order_by(
            timeline_events.c.event_time.desc(),
            timeline_events.c.id.desc(),
        )
        .limit(limit)
    )

    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()

    return [_decorate_event(row) for row in rows]


def recent_events(
    person_ids,
    *,
    limit: int = 20,
    event_types=None,
    start=None,
    end=None,
):
    """Authoritative multi-person timeline read for record-scoped dashboards.

    ``person_ids`` scopes the read: ``None`` = unrestricted (a ``record.read_all``
    caller), an empty collection = no accessible people (returns ``[]``), otherwise
    only events for those person ids. Optional ``event_types`` filter and
    ``start``/``end`` (half-open ``[start, end)``) time window. Read-only; does not
    create timeline data. Ordered newest-first.
    """
    if person_ids is not None and len(person_ids) == 0:
        return []
    statement = select(timeline_events)
    if person_ids is not None:
        statement = statement.where(timeline_events.c.person_id.in_(tuple(person_ids)))
    if event_types:
        statement = statement.where(timeline_events.c.event_type.in_(tuple(event_types)))
    if start is not None:
        statement = statement.where(timeline_events.c.event_time >= start)
    if end is not None:
        statement = statement.where(timeline_events.c.event_time < end)
    statement = statement.order_by(
        timeline_events.c.event_time.desc(), timeline_events.c.id.desc()
    ).limit(limit)

    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()

    return [_decorate_event(row) for row in rows]
