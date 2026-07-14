from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db import engine, people
from app.services.timeline import get_person_timeline


router = APIRouter(prefix="/timeline")
templates = Jinja2Templates(directory="app/templates")


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