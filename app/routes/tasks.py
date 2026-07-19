from datetime import date
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, select

from app.db import engine, people, record_assignments, tasks, users
from app.security.dependencies import current_principal
from app.security.models import Principal
from app.services.tasks import assignable_users, complete_task, create_task

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _tasks_with_assignee(connection, person_id):
    """Person's tasks with the current primary assignee resolved from record_assignments."""
    return connection.execute(
        select(tasks, users.c.display_name.label("assignee_name"), record_assignments.c.user_id.label("assignee_user_id"))
        .select_from(
            tasks
            .outerjoin(record_assignments, and_(
                record_assignments.c.entity_type == "task",
                record_assignments.c.entity_id == tasks.c.id,
                record_assignments.c.assignment_type == "primary",
                record_assignments.c.inactive_date.is_(None),
            ))
            .outerjoin(users, users.c.id == record_assignments.c.user_id)
        )
        .where(tasks.c.person_id == person_id)
        .order_by(tasks.c.status, tasks.c.due_date.asc().nullslast(), tasks.c.created_at.desc())
    ).mappings().all()


@router.get("/people/{person_id}/tasks", response_class=HTMLResponse)
def person_tasks(request: Request, person_id: int, principal: Principal = Depends(current_principal)):
    with engine.connect() as connection:
        person = connection.execute(
            select(people).where(people.c.id == person_id)
        ).mappings().one_or_none()
        if person is None:
            return HTMLResponse("<h1>Person not found</h1>", status_code=404)
        task_rows = _tasks_with_assignee(connection, person_id)
        users_list = assignable_users(connection)

    return templates.TemplateResponse(
        request=request,
        name="people/tasks.html",
        context={
            "person": person,
            "tasks": task_rows,
            "assignable_users": users_list,
            "created": request.query_params.get("created") == "1",
            "completed": request.query_params.get("completed") == "1",
        },
    )


@router.post("/people/{person_id}/tasks")
async def create_person_task(request: Request, person_id: int,
                             principal: Principal = Depends(current_principal)):
    form = parse_qs((await request.body()).decode("utf-8"))
    title = form.get("title", [""])[0].strip()
    if not title:
        return HTMLResponse("<h1>Task title is required</h1>", status_code=400)
    due_text = form.get("due_date", [""])[0].strip()
    assignee_raw = form.get("assigned_to_user_id", [""])[0].strip()
    try:
        create_task(
            person_id, title=title,
            description=form.get("description", [""])[0].strip() or None,
            priority=form.get("priority", ["normal"])[0],
            assigned_to_user_id=int(assignee_raw) if assignee_raw else None,
            due_date=date.fromisoformat(due_text) if due_text else None,
            actor_user_id=principal.user_id, request_id=_request_id(request), source="profile",
        )
    except ValueError as exc:
        return HTMLResponse(f"<h1>{exc}</h1>", status_code=400)
    return RedirectResponse(url=f"/people/{person_id}/tasks?created=1", status_code=303)


@router.post("/people/{person_id}/tasks/{task_id}/complete")
def complete_person_task(request: Request, person_id: int, task_id: int,
                         principal: Principal = Depends(current_principal)):
    found = complete_task(person_id, task_id, actor_user_id=principal.user_id, request_id=_request_id(request))
    if not found:
        return HTMLResponse("<h1>Task not found</h1>", status_code=404)
    return RedirectResponse(url=f"/people/{person_id}/tasks?completed=1", status_code=303)
