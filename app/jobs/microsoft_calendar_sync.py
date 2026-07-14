from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests
from sqlalchemy import select

from app.db import engine, microsoft_accounts, people
from app.services.timeline import add_timeline_event


GRAPH_CALENDAR_VIEW_URL = (
    "https://graph.microsoft.com/v1.0/me/calendarView"
)


def _normalize_email(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _parse_graph_datetime(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed


def _event_addresses(event: dict[str, Any]) -> set[str]:
    addresses = {
        _normalize_email(
            event.get("organizer", {})
            .get("emailAddress", {})
            .get("address")
        )
    }

    addresses.update(
        _normalize_email(
            attendee.get("emailAddress", {}).get("address")
        )
        for attendee in event.get("attendees", [])
    )

    addresses.discard("")
    return addresses


def sync_calendar_events(
    *,
    days_back: int = 30,
    days_forward: int = 90,
    top: int = 100,
) -> dict[str, int]:
    """Publish calendar events involving known clients to their timelines."""
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

    expires_at = account["expires_at"]

    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise RuntimeError(
            "The Microsoft access token has expired. "
            "Reconnect Microsoft 365 before syncing."
        )

    access_token = account["access_token"]

    if not access_token:
        raise RuntimeError("The Microsoft account has no access token.")

    person_by_email: dict[str, int] = {}

    for person in person_rows:
        for candidate in (
            person["normalized_email"],
            person["primary_email"],
        ):
            normalized = _normalize_email(candidate)

            if normalized:
                person_by_email[normalized] = person["id"]

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
                "isCancelled,isOnlineMeeting,webLink,bodyPreview"
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
    events = response.json().get("value", [])
    owner_email = _normalize_email(account["email"])
    matched_events = 0
    unmatched_events = 0
    cancelled_events = 0
    published_events = 0

    for event in events:
        if event.get("isCancelled"):
            cancelled_events += 1
            continue

        event_id = event.get("id")

        if not event_id:
            continue

        addresses = _event_addresses(event)
        addresses.discard(owner_email)
        person_ids = {
            person_by_email[address]
            for address in addresses
            if address in person_by_email
        }

        if not person_ids:
            unmatched_events += 1
            continue

        matched_events += 1
        subject = event.get("subject") or "(No subject)"
        preview = (event.get("bodyPreview") or "").strip()
        location = (
            event.get("location", {}).get("displayName") or None
        )
        start = event.get("start", {}).get("dateTime")
        end = event.get("end", {}).get("dateTime")

        if len(preview) > 500:
            preview = preview[:497] + "..."

        for person_id in person_ids:
            add_timeline_event(
                person_id=person_id,
                source="microsoft",
                event_type="calendar_event",
                title=subject,
                summary=preview or None,
                event_time=_parse_graph_datetime(start),
                external_id=(
                    f"outlook-calendar-{event_id}-person-{person_id}"
                ),
                event_metadata={
                    "microsoft_event_id": event_id,
                    "start": start,
                    "end": end,
                    "location": location,
                    "web_link": event.get("webLink"),
                    "is_online_meeting": bool(
                        event.get("isOnlineMeeting")
                    ),
                    "participant_addresses": sorted(addresses),
                },
            )
            published_events += 1

    return {
        "events_reviewed": len(events),
        "matched_events": matched_events,
        "unmatched_events": unmatched_events,
        "cancelled_events": cancelled_events,
        "published_events": published_events,
    }


if __name__ == "__main__":
    print(sync_calendar_events())
