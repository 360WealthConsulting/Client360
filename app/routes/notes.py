import uuid
from datetime import date
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db import engine, people
from app.security.audit import write_audit_event
from app.security.dependencies import current_principal
from app.security.models import Principal
from app.services.notes import (
    ACTIVITY_NOTE_TYPES,
    add_person_note,
    get_permanent_note,
    list_person_notes,
    save_permanent_note,
)
from app.services.tasks import assignable_users, create_task
from app.services.timeline import add_timeline_event

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or f"note-{uuid.uuid4()}"


@router.get("/people/{person_id}/notes", response_class=HTMLResponse)
def person_notes(request: Request, person_id: int, principal: Principal = Depends(current_principal)):
    with engine.connect() as connection:
        person = connection.execute(
            select(people).where(people.c.id == person_id)
        ).mappings().one_or_none()
        if person is None:
            return HTMLResponse("<h1>Person not found</h1>", status_code=404)
        users_list = assignable_users(connection)

    return templates.TemplateResponse(
        request=request,
        name="people/notes.html",
        context={
            "person": person,
            "assignable_users": users_list,
            "permanent": get_permanent_note(person_id),
            "activity_notes": list_person_notes(person_id),
            "saved": request.query_params.get("saved"),
            "log": request.query_params.get("log"),
        },
    )


@router.post("/people/{person_id}/notes")
async def post_person_notes(
    request: Request,
    person_id: int,
    principal: Principal = Depends(current_principal),
):
    with engine.connect() as connection:
        exists = connection.execute(
            select(people.c.id).where(people.c.id == person_id)
        ).scalar_one_or_none()
    if exists is None:
        return HTMLResponse("<h1>Person not found</h1>", status_code=404)

    form = parse_qs((await request.body()).decode("utf-8"))
    kind = form.get("kind", [""])[0]

    if kind == "permanent":
        body = form.get("body", [""])[0]
        save_permanent_note(person_id, body, editor_user_id=principal.user_id)
        write_audit_event(
            action="note.permanent.updated", entity_type="person", entity_id=person_id,
            actor_user_id=principal.user_id, request_id=_request_id(request),
            metadata={},
        )
        return RedirectResponse(url=f"/people/{person_id}/notes?saved=permanent", status_code=303)

    # default branch: one shared path for an append-only activity note OR a one-click logged
    # communication (call/email/meeting), distinguished by a validated note_type. Append-only
    # inserts mean simultaneous adds never overwrite. Timeline + audit are written here once
    # (the notes service records neither), so there is no duplicate event.
    body = form.get("note", [""])[0].strip()
    if not body:
        return RedirectResponse(url=f"/people/{person_id}/notes", status_code=303)
    note_type = form.get("note_type", ["note"])[0]
    if note_type not in ACTIVITY_NOTE_TYPES:
        note_type = "note"
    note_id = add_person_note(person_id, body, author_user_id=principal.user_id, note_type=note_type)
    summary = body if len(body) <= 500 else body[:497] + "..."
    is_comm = note_type != "note"
    if is_comm:
        event_type, title, audit_action = "communication_logged", f"{note_type.title()} logged", "communication.logged"
    else:
        event_type, title, audit_action = "activity_note_added", "Activity note added", "note.activity.added"
    add_timeline_event(
        person_id=person_id, source="client360", event_type=event_type,
        title=title, summary=summary,
        event_metadata={"note_id": note_id, "note_type": note_type, "author_user_id": principal.user_id},
    )
    write_audit_event(
        action=audit_action, entity_type="person", entity_id=person_id,
        actor_user_id=principal.user_id, request_id=_request_id(request),
        metadata={"note_id": note_id, "note_type": note_type},
    )

    # optional follow-up task from the activity note (reuses the person-task service/table
    # and the canonical user-assignment model)
    if form.get("create_task", [""])[0] and form.get("task_title", [""])[0].strip():
        due_text = form.get("task_due_date", [""])[0].strip()
        assignee_raw = form.get("task_assigned_to_user_id", [""])[0].strip()
        create_task(
            person_id, title=form.get("task_title", [""])[0].strip(),
            priority=form.get("task_priority", ["normal"])[0],
            assigned_to_user_id=int(assignee_raw) if assignee_raw else None,
            due_date=date.fromisoformat(due_text) if due_text else None,
            actor_user_id=principal.user_id, request_id=_request_id(request),
            source="activity_note", source_note_id=note_id,
        )

    return RedirectResponse(
        url=f"/people/{person_id}/notes?saved={'logged' if is_comm else 'activity'}", status_code=303)
