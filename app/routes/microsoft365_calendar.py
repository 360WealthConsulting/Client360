from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, update

from app.db import engine, microsoft_unmatched_calendar_attendees, people
from app.jobs.microsoft_calendar_sync import (
    calendar_external_id,
    sync_calendar_events,
)
from app.services.timeline import add_timeline_event


router = APIRouter(prefix="/microsoft365")
templates = Jinja2Templates(directory="app/templates")


@router.post("/calendar/sync")
def sync_microsoft365_calendar(
    days_back: int = Query(default=30, ge=0, le=365),
    days_forward: int = Query(default=90, ge=1, le=365),
):
    try:
        return sync_calendar_events(
            days_back=days_back,
            days_forward=days_forward,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/calendar-review", response_class=HTMLResponse)
def calendar_review(request: Request):
    with engine.connect() as connection:
        attendees = connection.execute(
            select(microsoft_unmatched_calendar_attendees)
            .where(
                microsoft_unmatched_calendar_attendees.c.status == "pending"
            )
            .order_by(
                microsoft_unmatched_calendar_attendees.c.starts_at.desc(),
                microsoft_unmatched_calendar_attendees.c.id.desc(),
            )
            .limit(100)
        ).mappings().all()
        person_rows = connection.execute(
            select(people.c.id, people.c.full_name, people.c.primary_email)
            .where(people.c.active.is_(True))
            .order_by(people.c.last_name, people.c.first_name, people.c.id)
        ).mappings().all()

    return templates.TemplateResponse(
        request=request,
        name="microsoft365/calendar_review.html",
        context={"attendees": attendees, "people": person_rows},
    )


@router.post("/calendar-review/{review_id}/ignore")
def ignore_calendar_attendee(review_id: int):
    with engine.begin() as connection:
        result = connection.execute(
            update(microsoft_unmatched_calendar_attendees)
            .where(
                microsoft_unmatched_calendar_attendees.c.id == review_id,
                microsoft_unmatched_calendar_attendees.c.status == "pending",
            )
            .values(status="ignored")
        )

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Review item not found.")

    return RedirectResponse(
        url="/microsoft365/calendar-review",
        status_code=303,
    )


@router.post("/calendar-review/{review_id}/match/{person_id}")
def match_calendar_attendee(review_id: int, person_id: int):
    with engine.connect() as connection:
        review_item = connection.execute(
            select(microsoft_unmatched_calendar_attendees).where(
                microsoft_unmatched_calendar_attendees.c.id == review_id,
                microsoft_unmatched_calendar_attendees.c.status == "pending",
            )
        ).mappings().one_or_none()
        person_exists = connection.execute(
            select(people.c.id).where(people.c.id == person_id)
        ).scalar_one_or_none()

    if review_item is None:
        raise HTTPException(status_code=404, detail="Review item not found.")
    if person_exists is None:
        raise HTTPException(status_code=404, detail="Person not found.")

    add_timeline_event(
        person_id=person_id,
        source="microsoft",
        event_type="calendar_event",
        title=review_item["subject"] or "(No subject)",
        summary=(review_item["event_metadata"] or {}).get("body_preview"),
        event_time=review_item["starts_at"],
        external_id=calendar_external_id(
            review_item["microsoft_event_id"], person_id
        ),
        event_metadata=dict(review_item["event_metadata"] or {}),
    )

    with engine.begin() as connection:
        connection.execute(
            update(microsoft_unmatched_calendar_attendees)
            .where(microsoft_unmatched_calendar_attendees.c.id == review_id)
            .values(status="matched", matched_person_id=person_id)
        )

    return RedirectResponse(
        url="/microsoft365/calendar-review",
        status_code=303,
    )
