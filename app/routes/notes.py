from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db import engine, people
from app.services.notes import get_person_notes, save_person_notes
from app.services.timeline import add_timeline_event


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get(
    "/people/{person_id}/notes",
    response_class=HTMLResponse,
)
def person_notes(request: Request, person_id: int):
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

    notes = get_person_notes(person_id)

    return templates.TemplateResponse(
        request=request,
        name="people/notes.html",
        context={
            "person": person,
            "notes": notes,
            "saved": request.query_params.get("saved") == "1",
        },
    )


@router.post("/people/{person_id}/notes")
async def update_person_notes(
    request: Request,
    person_id: int,
):
    with engine.connect() as connection:
        person_exists = connection.execute(
            select(people.c.id).where(
                people.c.id == person_id
            )
        ).scalar_one_or_none()

    if person_exists is None:
        return HTMLResponse(
            "<h1>Person not found</h1>",
            status_code=404,
        )

    body = (await request.body()).decode("utf-8")
    form_data = parse_qs(body)
    notes = form_data.get("notes", [""])[0]
    previous_notes = get_person_notes(person_id)

    save_person_notes(person_id, notes)

    if notes != previous_notes:
        summary = notes.strip()

        if not summary:
            summary = "Advisor notes were cleared."
        elif len(summary) > 500:
            summary = summary[:497] + "..."

        add_timeline_event(
            person_id=person_id,
            source="client360",
            event_type="note_updated",
            title="Advisor Notes Updated",
            summary=summary,
            event_metadata={
                "previous_length": len(previous_notes),
                "new_length": len(notes),
            },
        )

    return RedirectResponse(
        url=f"/people/{person_id}/notes?saved=1",
        status_code=303,
    )
