from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import func, select

from app.db import (
    activities,
    documents,
    engine,
    tasks,
    timeline_events,
)


def _days_since(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    return max((now - value).days, 0)


def get_client_summary(person_id: int) -> Dict[str, Any]:
    today = date.today()

    with engine.connect() as connection:
        open_task_count = connection.execute(
            select(func.count())
            .select_from(tasks)
            .where(
                tasks.c.person_id == person_id,
                tasks.c.status != "complete",
            )
        ).scalar_one()

        overdue_task_count = connection.execute(
            select(func.count())
            .select_from(tasks)
            .where(
                tasks.c.person_id == person_id,
                tasks.c.status != "complete",
                tasks.c.due_date.is_not(None),
                tasks.c.due_date < today,
            )
        ).scalar_one()

        recent_activity_count = connection.execute(
            select(func.count())
            .select_from(activities)
            .where(
                activities.c.person_id == person_id
            )
        ).scalar_one()

        recent_document_count = connection.execute(
            select(func.count())
            .select_from(documents)
            .where(
                documents.c.person_id == person_id,
                documents.c.archived.is_(False),
            )
        ).scalar_one()

        latest_event = connection.execute(
            select(
                timeline_events.c.event_time,
                timeline_events.c.event_type,
                timeline_events.c.title,
            )
            .where(
                timeline_events.c.person_id == person_id
            )
            .order_by(
                timeline_events.c.event_time.desc(),
                timeline_events.c.id.desc(),
            )
            .limit(1)
        ).mappings().one_or_none()

    last_contact_at = (
        latest_event["event_time"]
        if latest_event
        else None
    )

    return {
        "open_task_count": open_task_count,
        "overdue_task_count": overdue_task_count,
        "activity_count": recent_activity_count,
        "document_count": recent_document_count,
        "last_contact_at": last_contact_at,
        "days_since_last_contact": _days_since(last_contact_at),
        "last_event_type": (
            latest_event["event_type"]
            if latest_event
            else None
        ),
        "last_event_title": (
            latest_event["title"]
            if latest_event
            else None
        ),
    }
