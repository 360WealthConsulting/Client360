from datetime import datetime, timezone
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import insert, select

from app.db import activities, engine, people


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get(
    "/people/{person_id}/activities",
    response_class=HTMLResponse,
)
def person_activities(request: Request, person_id: int):
    with engine.connect() as connection:
        person = connection.execute(
            select(people).where(people.c.id == person_id)
        ).mappings().one_or_none()

        if person is None:
            return HTMLResponse(
                "<h1>Person not found</h1>",
                status_code=404,
            )

        activity_rows = connection.execute(
            select(activities)
            .where(activities.c.person_id == person_id)
            .order_by(
                activities.c.occurred_at.desc(),
                activities.c.id.desc(),
            )
        ).mappings().all()

    return templates.TemplateResponse(
        request=request,
        name="people/activities.html",
        context={
            "person": person,
            "activities": activity_rows,
            "created": request.query_params.get("created") == "1",
        },
    )


@router.post("/people/{person_id}/activities")
async def create_person_activity(
    request: Request,
    person_id: int,
):
    body = (await request.body()).decode("utf-8")
    form_data = parse_qs(body)

    activity_type = form_data.get(
        "activity_type",
        ["note"],
    )[0].strip()

    title = form_data.get("title", [""])[0].strip()
    details = form_data.get("details", [""])[0].strip()
    created_by = form_data.get("created_by", [""])[0].strip()
    occurred_at_text = form_data.get(
        "occurred_at",
        [""],
    )[0].strip()

    if not title:
        return HTMLResponse(
            "<h1>Activity title is required</h1>",
            status_code=400,
        )

    occurred_at = (
        datetime.fromisoformat(occurred_at_text).replace(
            tzinfo=timezone.utc
        )
        if occurred_at_text
        else datetime.now(timezone.utc)
    )

    with engine.begin() as connection:
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

        connection.execute(
            insert(activities).values(
                person_id=person_id,
                activity_type=activity_type,
                title=title,
                details=details or None,
                occurred_at=occurred_at,
                created_by=created_by or None,
            )
        )

    return RedirectResponse(
        url=f"/people/{person_id}/activities?created=1",
        status_code=303,
    )
