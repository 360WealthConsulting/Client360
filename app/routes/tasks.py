from datetime import date, datetime, timezone
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import insert, select, update

from app.db import engine, people, tasks


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get(
    "/people/{person_id}/tasks",
    response_class=HTMLResponse,
)
def person_tasks(request: Request, person_id: int):
    with engine.connect() as connection:
        person = connection.execute(
            select(people).where(people.c.id == person_id)
        ).mappings().one_or_none()

        if person is None:
            return HTMLResponse(
                "<h1>Person not found</h1>",
                status_code=404,
            )

        task_rows = connection.execute(
            select(tasks)
            .where(tasks.c.person_id == person_id)
            .order_by(
                tasks.c.status,
                tasks.c.due_date.asc().nullslast(),
                tasks.c.created_at.desc(),
            )
        ).mappings().all()

    return templates.TemplateResponse(
        request=request,
        name="people/tasks.html",
        context={
            "person": person,
            "tasks": task_rows,
            "created": request.query_params.get("created") == "1",
            "completed": request.query_params.get("completed") == "1",
        },
    )


@router.post("/people/{person_id}/tasks")
async def create_person_task(
    request: Request,
    person_id: int,
):
    body = (await request.body()).decode("utf-8")
    form_data = parse_qs(body)

    title = form_data.get("title", [""])[0].strip()
    description = form_data.get("description", [""])[0].strip()
    priority = form_data.get("priority", ["normal"])[0]
    assigned_to = form_data.get("assigned_to", [""])[0].strip()
    due_date_text = form_data.get("due_date", [""])[0].strip()

    if not title:
        return HTMLResponse(
            "<h1>Task title is required</h1>",
            status_code=400,
        )

    due_date = (
        date.fromisoformat(due_date_text)
        if due_date_text
        else None
    )

    with engine.begin() as connection:
        person_exists = connection.execute(
            select(people.c.id).where(people.c.id == person_id)
        ).scalar_one_or_none()

        if person_exists is None:
            return HTMLResponse(
                "<h1>Person not found</h1>",
                status_code=404,
            )

        connection.execute(
            insert(tasks).values(
                person_id=person_id,
                title=title,
                description=description or None,
                status="open",
                priority=priority,
                assigned_to=assigned_to or None,
                due_date=due_date,
            )
        )

    return RedirectResponse(
        url=f"/people/{person_id}/tasks?created=1",
        status_code=303,
    )


@router.post(
    "/people/{person_id}/tasks/{task_id}/complete"
)
def complete_person_task(
    person_id: int,
    task_id: int,
):
    with engine.begin() as connection:
        result = connection.execute(
            update(tasks)
            .where(
                tasks.c.id == task_id,
                tasks.c.person_id == person_id,
            )
            .values(
                status="complete",
                completed_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )

    if result.rowcount == 0:
        return HTMLResponse(
            "<h1>Task not found</h1>",
            status_code=404,
        )

    return RedirectResponse(
        url=f"/people/{person_id}/tasks?completed=1",
        status_code=303,
    )
