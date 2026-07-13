from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db import activities, engine, people


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/activities")
def activity_dashboard(request: Request):
    with engine.connect() as connection:
        activity_rows = connection.execute(
            select(
                activities,
                people.c.full_name.label("person_name"),
            )
            .join(
                people,
                people.c.id == activities.c.person_id,
            )
            .order_by(
                activities.c.occurred_at.desc(),
                activities.c.id.desc(),
            )
        ).mappings().all()

    return templates.TemplateResponse(
        request=request,
        name="activities/dashboard.html",
        context={
            "activities": activity_rows,
        },
    )
