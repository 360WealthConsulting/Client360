from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db import engine, people
from app.services.timeline import (
    add_timeline_event,
    get_person_timeline,
)


router = APIRouter(prefix="/timeline")
templates = Jinja2Templates(directory="app/templates")


@router.post("/test")
def create_test_timeline_event():
    event_id = add_timeline_event(
        person_id=1,
        source="system",
        event_type="test",
        title="Timeline Engine Online",
        summary="First timeline event created successfully.",
        external_id="timeline-engine-test-person-1",
    )

    return {
        "status": "created",
        "event_id": event_id,
    }


@router.get(
    "/person/{person_id}",
    response_class=HTMLResponse,
)
def person_timeline(
    request: Request,
    person_id: int,
):
    with engine.connect() as connection:
        person = connection.execute(
            select(people).where(
                people.c.id == person_id
            )
        ).mappings().one_or_none()

    if person is None:
        return HTMLResponse(
            "<h1>Person not found</h1>",
            status_code=404,
        )

    events = get_person_timeline(person_id)

    return templates.TemplateResponse(
        request=request,
        name="people/timeline.html",
        context={
            "person": person,
            "events": events,
        },
    )