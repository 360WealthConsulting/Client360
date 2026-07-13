from datetime import datetime, timezone
from typing import Any, Optional

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import (
    engine,
    microsoft_accounts,
    microsoft_unmatched_messages,
    people,
)
from app.services.timeline import add_timeline_event


GRAPH_MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"


def _normalize_email(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _parse_graph_datetime(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)

    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


def sync_recent_mail(top: int = 50) -> dict[str, Any]:
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
        raise RuntimeError(
            "No Microsoft 365 account is connected."
        )

    expires_at = account["expires_at"]

    if (
        expires_at is not None
        and expires_at <= datetime.now(timezone.utc)
    ):
        raise RuntimeError(
            "The Microsoft access token has expired. "
            "Reconnect Microsoft 365 before syncing."
        )

    access_token = account["access_token"]

    if not access_token:
        raise RuntimeError(
            "The Microsoft account has no access token."
        )

    person_by_email: dict[str, int] = {}

    for person in person_rows:
        for candidate in (
            person["normalized_email"],
            person["primary_email"],
        ):
            normalized = _normalize_email(candidate)

            if normalized:
                person_by_email[normalized] = person["id"]

    response = requests.get(
        GRAPH_MESSAGES_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        params={
            "$top": str(top),
            "$select": (
                "id,subject,from,receivedDateTime,"
                "bodyPreview,webLink,hasAttachments,isRead"
            ),
            "$orderby": "receivedDateTime desc",
        },
        timeout=30,
    )

    if response.status_code == 401:
        raise RuntimeError(
            "Microsoft rejected the access token. "
            "Reconnect Microsoft 365 before syncing."
        )

    response.raise_for_status()

    messages = response.json().get("value", [])

    matched = 0
    unmatched = 0
    published = 0

    for message in messages:
        sender = (
            message.get("from", {})
            .get("emailAddress", {})
        )

        sender_address = _normalize_email(
            sender.get("address")
        )
        sender_name = sender.get("name") or sender_address

        person_id = person_by_email.get(sender_address)

        if person_id is None:
            unmatched += 1

            message_id = message.get("id")

            if message_id:
                statement = (
                    pg_insert(microsoft_unmatched_messages)
                    .values(
                        microsoft_message_id=message_id,
                        sender_name=sender_name,
                        sender_address=sender_address,
                        subject=message.get("subject"),
                        body_preview=message.get("bodyPreview"),
                        received_at=_parse_graph_datetime(
                            message.get("receivedDateTime")
                        ),
                        web_link=message.get("webLink"),
                        has_attachments=bool(
                            message.get("hasAttachments")
                        ),
                        status="pending",
                    )
                    .on_conflict_do_update(
                        constraint=(
                            "uq_microsoft_unmatched_message_id"
                        ),
                        set_={
                            "sender_name": sender_name,
                            "sender_address": sender_address,
                            "subject": message.get("subject"),
                            "body_preview": message.get(
                                "bodyPreview"
                            ),
                            "received_at": (
                                _parse_graph_datetime(
                                    message.get(
                                        "receivedDateTime"
                                    )
                                )
                            ),
                            "web_link": message.get("webLink"),
                            "has_attachments": bool(
                                message.get(
                                    "hasAttachments"
                                )
                            ),
                            "updated_at": datetime.now(
                                timezone.utc
                            ),
                        },
                    )
                )

                with engine.begin() as connection:
                    connection.execute(statement)

            continue

        matched += 1

        message_id = message.get("id")

        if not message_id:
            continue

        subject = message.get("subject") or "(No subject)"
        preview = (message.get("bodyPreview") or "").strip()

        if len(preview) > 500:
            preview = preview[:497] + "..."

        add_timeline_event(
            person_id=person_id,
            source="microsoft",
            event_type="email_received",
            title=subject,
            summary=preview or None,
            event_time=_parse_graph_datetime(
                message.get("receivedDateTime")
            ),
            external_id=f"outlook-message-{message_id}",
            event_metadata={
                "sender_name": sender_name,
                "sender_address": sender_address,
                "web_link": message.get("webLink"),
                "has_attachments": bool(
                    message.get("hasAttachments")
                ),
                "is_read": bool(message.get("isRead")),
                "microsoft_message_id": message_id,
            },
        )

        published += 1

    return {
        "messages_reviewed": len(messages),
        "matched_messages": matched,
        "unmatched_messages": unmatched,
        "published_events": published,
    }


if __name__ == "__main__":
    result = sync_recent_mail()

    print("Microsoft mail sync complete.")

    for key, value in result.items():
        print(f"{key}: {value}")
