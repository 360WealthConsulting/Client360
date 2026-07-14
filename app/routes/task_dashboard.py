from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db import engine, people, tasks

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/tasks")
def task_dashboard(request: Request, limit: int = 100, offset: int = 0):
    # Bound the read so the page is O(page size), not O(all tasks) (RC9).
    limit = max(1, min(limit, 500)); offset = max(0, offset)
    with engine.connect() as connection:
        task_rows = connection.execute(
            select(
                tasks,
                people.c.full_name.label("person_name"),
            )
            .join(
                people,
                people.c.id == tasks.c.person_id,
            )
            .order_by(
                tasks.c.status,
                tasks.c.priority.desc(),
                tasks.c.due_date.asc().nullslast(),
            )
            .limit(limit)
            .offset(offset)
        ).mappings().all()

    return templates.TemplateResponse(
        request=request,
        name="tasks/dashboard.html",
        context={
            "tasks": task_rows,
            "limit": limit,
            "offset": offset,
        },
    )
