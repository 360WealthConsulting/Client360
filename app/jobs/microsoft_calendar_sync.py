from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import (
    engine,
    microsoft_accounts,
    microsoft_unmatched_calendar_attendees,
    people,
)
from app.services.microsoft_identity import get_microsoft_access_token, record_sync_health
from app.services.timeline import add_timeline_event


GRAPH_CALENDAR_VIEW_URL = (
    "https://graph.microsoft.com/v1.0/me/calendarView"
)


def normalize_email(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def parse_graph_datetime(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def build_person_email_index(
    people_rows: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    index: dict[str, int] = {}
    ambiguous: set[str] = set()

    for person in people_rows:
        for candidate in (
            person.get("normalized_email"),
            person.get("primary_email"),
        ):
            normalized = normalize_email(candidate)

            if normalized:
                person_id = int(person["id"])
                existing_person_id = index.get(normalized)

                if (
                    existing_person_id is not None
                    and existing_person_id != person_id
                ):
                    ambiguous.add(normalized)
                else:
                    index[normalized] = person_id

    for email in ambiguous:
        index.pop(email, None)

    return index


def event_participants(event: Mapping[str, Any]) -> list[dict[str, str]]:
    participants: dict[str, dict[str, str]] = {}
    organizer = event.get("organizer", {}).get("emailAddress", {})
    organizer_email = normalize_email(organizer.get("address"))

    if organizer_email:
        participants[organizer_email] = {
            "email": organizer_email,
            "name": organizer.get("name") or organizer_email,
            "role": "organizer",
            "response_status": "organizer",
        }

    for attendee in event.get("attendees", []):
        email_address = attendee.get("emailAddress", {})
        email = normalize_email(email_address.get("address"))

        if not email:
            continue

        response_status = (
            attendee.get("status", {}).get("response") or "none"
        )
        participants[email] = {
            "email": email,
            "name": email_address.get("name") or email,
            "role": attendee.get("type") or "required",
            "response_status": response_status,
        }

    return list(participants.values())


def calendar_external_id(event_id: str, person_id: int) -> str:
    return f"outlook-calendar-{event_id}-person-{person_id}"


def build_timeline_metadata(event: Mapping[str, Any]) -> dict[str, Any]:
    organizer = event.get("organizer", {}).get("emailAddress", {})
    attendees = [
        {
            "name": attendee.get("emailAddress", {}).get("name"),
            "email": normalize_email(
                attendee.get("emailAddress", {}).get("address")
            ),
            "type": attendee.get("type"),
            "response_status": attendee.get("status", {}).get("response"),
        }
        for attendee in event.get("attendees", [])
    ]

    return {
        "microsoft_event_id": event.get("id"),
        "subject": event.get("subject") or "(No subject)",
        "body_preview": (event.get("bodyPreview") or "").strip(),
        "organizer": {
            "name": organizer.get("name"),
            "email": normalize_email(organizer.get("address")),
        },
        "attendees": attendees,
        "start": event.get("start"),
        "end": event.get("end"),
        "location": event.get("location", {}).get("displayName"),
        "online_meeting_link": (
            event.get("onlineMeeting", {}).get("joinUrl")
        ),
        "web_link": event.get("webLink"),
        "response_status": event.get("responseStatus", {}).get("response"),
        "is_online_meeting": bool(event.get("isOnlineMeeting")),
    }


def process_calendar_events(
    events: Iterable[Mapping[str, Any]],
    *,
    owner_email: str,
    person_by_email: Mapping[str, int],
    publish: Callable[..., Any],
    queue_unmatched: Callable[..., Any],
    resolve_match: Callable[..., Any],
) -> dict[str, int]:
    reviewed = matched = unmatched = cancelled = published = 0
    normalized_owner = normalize_email(owner_email)

    for event in events:
        reviewed += 1

        if event.get("isCancelled"):
            cancelled += 1
            continue

        event_id = event.get("id")

        if not event_id:
            continue

        participants = [
            participant
            for participant in event_participants(event)
            if participant["email"] != normalized_owner
        ]
        matched_person_ids: set[int] = set()
        unmatched_participants: list[dict[str, str]] = []

        for participant in participants:
            person_id = person_by_email.get(participant["email"])

            if person_id is None:
                unmatched_participants.append(participant)
            else:
                matched_person_ids.add(person_id)
                resolve_match(
                    event_id=event_id,
                    participant=participant,
                    person_id=person_id,
                )

        metadata = build_timeline_metadata(event)
        subject = event.get("subject") or "(No subject)"
        preview = (event.get("bodyPreview") or "").strip()

        if len(preview) > 500:
            preview = preview[:497] + "..."

        for person_id in matched_person_ids:
            publish(
                person_id=person_id,
                source="microsoft",
                event_type="calendar_event",
                title=subject,
                summary=preview or None,
                event_time=parse_graph_datetime(
                    event.get("start", {}).get("dateTime")
                ),
                external_id=calendar_external_id(event_id, person_id),
                event_metadata=metadata,
            )
            published += 1

        for participant in unmatched_participants:
            queue_unmatched(
                event=event,
                participant=participant,
                metadata=metadata,
            )
            unmatched += 1

        if matched_person_ids:
            matched += 1

    return {
        "events_reviewed": reviewed,
        "matched_events": matched,
        "unmatched_attendees": unmatched,
        "cancelled_events": cancelled,
        "published_events": published,
    }


def queue_unmatched_calendar_attendee(
    *,
    event: Mapping[str, Any],
    participant: Mapping[str, str],
    metadata: Mapping[str, Any],
) -> None:
    event_id = str(event["id"])
    values = {
        "microsoft_event_id": event_id,
        "attendee_email": participant["email"],
        "attendee_name": participant.get("name"),
        "attendee_role": participant.get("role"),
        "response_status": participant.get("response_status"),
        "subject": event.get("subject") or "(No subject)",
        "starts_at": parse_graph_datetime(
            event.get("start", {}).get("dateTime")
        ),
        "ends_at": parse_graph_datetime(
            event.get("end", {}).get("dateTime")
        ),
        "location": event.get("location", {}).get("displayName"),
        "online_meeting_link": (
            event.get("onlineMeeting", {}).get("joinUrl")
        ),
        "web_link": event.get("webLink"),
        "event_metadata": dict(metadata),
        "status": "pending",
    }
    update_values = {
        key: value
        for key, value in values.items()
        if key != "status"
    }
    statement = (
        pg_insert(microsoft_unmatched_calendar_attendees)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_microsoft_calendar_event_attendee",
            set_={
                **update_values,
                "updated_at": datetime.now(timezone.utc),
            },
        )
    )

    with engine.begin() as connection:
        connection.execute(statement)


def resolve_matched_calendar_attendee(
    *,
    event_id: str,
    participant: Mapping[str, str],
    person_id: int,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            microsoft_unmatched_calendar_attendees.update()
            .where(
                microsoft_unmatched_calendar_attendees.c.microsoft_event_id
                == event_id,
                microsoft_unmatched_calendar_attendees.c.attendee_email
                == participant["email"],
                microsoft_unmatched_calendar_attendees.c.status == "pending",
            )
            .values(status="matched", matched_person_id=person_id)
        )


def sync_calendar_events(
    *,
    days_back: int = 30,
    days_forward: int = 90,
    top: int = 100,
) -> dict[str, int]:
    """Sync recent and upcoming calendar events into Client360."""
    with engine.connect() as connection:
        account = connection.execute(
            select(microsoft_accounts)
            .order_by(microsoft_accounts.c.updated_at.desc())
            .limit(1)
        ).mappings().one_or_none()
        person_rows = connection.execute(
            select(
                people.c.id,
                people.c.primary_email,
                people.c.normalized_email,
            )
        ).mappings().all()

    if account is None:
        raise RuntimeError("No Microsoft 365 account is connected.")

    try:
        access_token = get_microsoft_access_token(account)
    except Exception as exc:
        record_sync_health(account["id"], "error", exc)
        raise

    now = datetime.now(timezone.utc)
    response = requests.get(
        GRAPH_CALENDAR_VIEW_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Prefer": 'outlook.timezone="UTC"',
        },
        params={
            "startDateTime": (now - timedelta(days=days_back)).isoformat(),
            "endDateTime": (now + timedelta(days=days_forward)).isoformat(),
            "$top": str(top),
            "$select": (
                "id,subject,start,end,location,organizer,attendees,"
                "isCancelled,isOnlineMeeting,onlineMeeting,webLink,"
                "bodyPreview,responseStatus"
            ),
            "$orderby": "start/dateTime",
        },
        timeout=30,
    )

    if response.status_code == 401:
        raise RuntimeError(
            "Microsoft rejected the access token. "
            "Reconnect Microsoft 365 before syncing."
        )

    response.raise_for_status()
    result = process_calendar_events(
        response.json().get("value", []),
        owner_email=account["email"],
        person_by_email=build_person_email_index(person_rows),
        publish=add_timeline_event,
        queue_unmatched=queue_unmatched_calendar_attendee,
        resolve_match=resolve_matched_calendar_attendee,
    )
    record_sync_health(account["id"], "ok")
    return result


if __name__ == "__main__":
    print(sync_calendar_events())
