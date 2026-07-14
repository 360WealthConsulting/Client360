from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import insert, select, update

from app.db import (
    engine,
    microsoft_unmatched_messages,
    people,
)
from app.services.timeline import add_timeline_event


router = APIRouter(prefix="/microsoft365")
templates = Jinja2Templates(directory="app/templates")


@router.get(
    "/inbox-review",
    response_class=HTMLResponse,
)
def inbox_review(request: Request):
    with engine.connect() as connection:
        messages = connection.execute(
            select(microsoft_unmatched_messages)
            .where(
                microsoft_unmatched_messages.c.status == "pending"
            )
            .order_by(
                microsoft_unmatched_messages.c.received_at.desc(),
                microsoft_unmatched_messages.c.id.desc(),
            )
            .limit(100)
        ).mappings().all()

        person_rows = connection.execute(
            select(
                people.c.id,
                people.c.full_name,
                people.c.primary_email,
            )
            .where(people.c.active.is_(True))
            .order_by(
                people.c.last_name,
                people.c.first_name,
                people.c.id,
            )
        ).mappings().all()

    return templates.TemplateResponse(
        request=request,
        name="microsoft365/inbox_review.html",
        context={
            "messages": messages,
            "people": person_rows,
        },
    )

@router.post("/inbox-review/{message_id}/ignore")
def ignore_message(message_id: int):
    with engine.begin() as connection:
        result = connection.execute(
            update(microsoft_unmatched_messages)
            .where(
                microsoft_unmatched_messages.c.id == message_id,
                microsoft_unmatched_messages.c.status == "pending",
            )
            .values(status="ignored")
        )

    if result.rowcount == 0:
        return HTMLResponse(
            "<h1>Message not found</h1>",
            status_code=404,
        )

    return RedirectResponse(
        url="/microsoft365/inbox-review",
        status_code=303,
    )

@router.post(
    "/inbox-review/{message_id}/match/{person_id}"
)
def match_message_to_person(
    message_id: int,
    person_id: int,
):
    with engine.begin() as connection:
        message = connection.execute(
            select(microsoft_unmatched_messages).where(
                microsoft_unmatched_messages.c.id == message_id,
                microsoft_unmatched_messages.c.status == "pending",
            )
        ).mappings().one_or_none()

        person_exists = connection.execute(
            select(people.c.id).where(
                people.c.id == person_id
            )
        ).scalar_one_or_none()

    if message is None:
        return HTMLResponse(
            "<h1>Message not found</h1>",
            status_code=404,
        )

    if person_exists is None:
        return HTMLResponse(
            "<h1>Person not found</h1>",
            status_code=404,
        )

    add_timeline_event(
        person_id=person_id,
        source="microsoft",
        event_type="email_received",
        title=message["subject"] or "(No subject)",
        summary=message["body_preview"] or None,
        event_time=message["received_at"],
        external_id=(
            f"outlook-message-"
            f"{message['microsoft_message_id']}"
        ),
        event_metadata={
            "sender_name": message["sender_name"],
            "sender_address": message["sender_address"],
            "web_link": message["web_link"],
            "has_attachments": message["has_attachments"],
            "microsoft_message_id": (
                message["microsoft_message_id"]
            ),
        },
    )

    with engine.begin() as connection:
        connection.execute(
            update(microsoft_unmatched_messages)
            .where(
                microsoft_unmatched_messages.c.id == message_id
            )
            .values(
                status="matched",
                matched_person_id=person_id,
            )
        )

    return RedirectResponse(
        url="/microsoft365/inbox-review",
        status_code=303,
    )

@router.post("/inbox-review/{message_id}/create-contact")
def create_contact_from_message(message_id: int):
    with engine.begin() as connection:
        message = connection.execute(
            select(microsoft_unmatched_messages).where(
                microsoft_unmatched_messages.c.id == message_id,
                microsoft_unmatched_messages.c.status == "pending",
            )
        ).mappings().one_or_none()

        if message is None:
            return HTMLResponse(
                "<h1>Message not found</h1>",
                status_code=404,
            )

        normalized_email = (
            message["sender_address"] or ""
        ).strip().lower()

        existing_person_id = connection.execute(
            select(people.c.id).where(
                people.c.normalized_email == normalized_email
            )
        ).scalar_one_or_none()

        if existing_person_id is not None:
            return HTMLResponse(
                "<h1>A contact with this email already exists</h1>",
                status_code=409,
            )

        display_name = (
            message["sender_name"]
            or message["sender_address"]
            or "Microsoft Contact"
        ).strip()

        person_id = connection.execute(
            insert(people)
            .values(
                full_name=display_name,
                primary_email=message["sender_address"],
                normalized_email=normalized_email,
                contact_type="prospect",
                active=True,
            )
            .returning(people.c.id)
        ).scalar_one()

    add_timeline_event(
        person_id=person_id,
        source="microsoft",
        event_type="email_received",
        title=message["subject"] or "(No subject)",
        summary=message["body_preview"] or None,
        event_time=message["received_at"],
        external_id=(
            f"outlook-message-"
            f"{message['microsoft_message_id']}"
        ),
        event_metadata={
            "sender_name": message["sender_name"],
            "sender_address": message["sender_address"],
            "web_link": message["web_link"],
            "has_attachments": message["has_attachments"],
            "microsoft_message_id": (
                message["microsoft_message_id"]
            ),
        },
    )

    with engine.begin() as connection:
        connection.execute(
            update(microsoft_unmatched_messages)
            .where(
                microsoft_unmatched_messages.c.id == message_id
            )
            .values(
                status="matched",
                matched_person_id=person_id,
            )
        )

    return RedirectResponse(
        url=f"/people/{person_id}",
        status_code=303,
    )

