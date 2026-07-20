"""Edit a client's canonical contact/address details (Sprint 2).

GET renders the edit form; POST applies the change through the people service (audited +
timelined). The auth middleware maps /people to client.read and infers client.write for the
POST, so editing requires the client.write capability.
"""
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db import engine, people
from app.security.dependencies import current_principal
from app.security.models import Principal
from app.services.people import EDITABLE_FIELDS, update_person_contact
from app.templating import render_error

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/people/{person_id}/edit", response_class=HTMLResponse)
def edit_person_form(request: Request, person_id: int,
                     principal: Principal = Depends(current_principal)):
    with engine.connect() as connection:
        person = connection.execute(
            select(people).where(people.c.id == person_id)
        ).mappings().one_or_none()
    if person is None:
        return render_error(request, 404, detail="Person not found.")
    return templates.TemplateResponse(
        request=request, name="people/edit.html", context={"person": person},
    )


@router.post("/people/{person_id}/edit")
async def edit_person_submit(request: Request, person_id: int,
                             principal: Principal = Depends(current_principal)):
    form = parse_qs((await request.body()).decode("utf-8"))
    updates = {field: form.get(field, [""])[0] for field in EDITABLE_FIELDS}
    try:
        update_person_contact(
            person_id, updates, actor_user_id=principal.user_id,
            request_id=getattr(request.state, "request_id", None),
        )
    except ValueError:
        return render_error(request, 404, detail="Person not found.")
    return RedirectResponse(f"/people/{person_id}?saved=1", status_code=303)
