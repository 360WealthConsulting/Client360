import uuid
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
    add_person_note,
    get_permanent_note,
    list_person_notes,
    save_permanent_note,
)
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

    return templates.TemplateResponse(
        request=request,
        name="people/notes.html",
        context={
            "person": person,
            "permanent": get_permanent_note(person_id),
            "activity_notes": list_person_notes(person_id),
            "saved": request.query_params.get("saved"),
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

    # default: add an append-only activity note (simultaneous adds never overwrite)
    body = form.get("note", [""])[0].strip()
    if not body:
        return RedirectResponse(url=f"/people/{person_id}/notes", status_code=303)
    note_id = add_person_note(person_id, body, author_user_id=principal.user_id, note_type="note")
    summary = body if len(body) <= 500 else body[:497] + "..."
    add_timeline_event(
        person_id=person_id, source="client360", event_type="activity_note_added",
        title="Activity note added", summary=summary,
        event_metadata={"note_id": note_id, "author_user_id": principal.user_id},
    )
    write_audit_event(
        action="note.activity.added", entity_type="person", entity_id=person_id,
        actor_user_id=principal.user_id, request_id=_request_id(request),
        metadata={"note_id": note_id},
    )

    return RedirectResponse(url=f"/people/{person_id}/notes?saved=activity", status_code=303)
