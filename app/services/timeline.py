from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import engine, timeline_events


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
        return connection.execute(statement).mappings().all()


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
        return connection.execute(statement).mappings().all()
