from datetime import datetime, timezone

from sqlalchemy import select

from app.db import engine, timeline_events


def get_person_calendar_events(
    person_id: int,
    *,
    upcoming_only: bool = False,
    limit: int = 50,
):
    statement = select(timeline_events).where(
        timeline_events.c.person_id == person_id,
        timeline_events.c.source == "microsoft",
        timeline_events.c.event_type == "calendar_event",
    )

    if upcoming_only:
        statement = statement.where(
            timeline_events.c.event_time >= datetime.now(timezone.utc)
        ).order_by(timeline_events.c.event_time.asc())
    else:
        statement = statement.order_by(timeline_events.c.event_time.desc())

    with engine.connect() as connection:
        return connection.execute(
            statement.limit(limit)
        ).mappings().all()
