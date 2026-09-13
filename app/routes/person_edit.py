"""Edit a client's canonical contact, identity and household details (Sprint 2; extended).

GET renders the edit form; POST applies the change through the canonical services — each of which
audits and timelines the write it performs. The auth middleware maps /people to client.read and
infers client.write for the POST, so editing requires the client.write capability, and the
same-origin check in ``app.security.middleware`` covers CSRF for this state-changing route.

FOUR SERVICES, NOT ONE, ON PURPOSE
----------------------------------
``people.update_person_contact``      contact + address, the ordinary edit
``people.correct_person_identity``    legal name — a governed identity correction
``people.correct_person_birth_date``  date of birth — the same, for the same reason
``people.set_person_household``       household membership, to an explicitly chosen household

``app.services.people`` deliberately keeps identity fields out of the contact path so that
changing who someone IS can never happen as a side effect of correcting how to REACH them. This
route submits one form but calls the right service per field, which keeps that boundary intact
while giving staff a single place to work.

ONE FORM, ONE TRANSACTION
-------------------------
All four run on a single connection inside one ``engine.begin()``. A date of birth this route
accepted but the service rejects, or a household id that vanished between rendering the form and
submitting it, rolls the contact fields back with it. A half-applied profile — new address saved,
new name refused — is the outcome that transaction prevents.

HOUSEHOLD: EXACTLY THE ONE THAT WAS CHOSEN
------------------------------------------
The household select carries real household ids, and ``set_person_household`` assigns that id and
no other. Nothing here creates a household, reuses one because a relative happens to have it, or
infers a family grouping from a surname. "No household" is a distinct choice in the list rather
than an empty submission, so removing an assignment is something staff pick on purpose and is
audited exactly like setting one.

SHARED EMAIL WARNS, IT NEVER RE-ANCHORS
---------------------------------------
Saving an address another active person already holds is allowed and reported. It does not merge
records, move correspondence, or choose between the two people — silently re-pointing one client's
mail onto another's record is precisely the outcome the warning exists to prevent.
"""
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db import engine, households, people
from app.security.dependencies import current_principal
from app.security.models import Principal
from app.services.people import (
    EDITABLE_FIELDS,
    _parse_birth_date,
    correct_person_birth_date,
    correct_person_identity,
    people_sharing_email,
    set_person_household,
    update_person_contact,
)
from app.templating import render_error

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

#: Submitted when staff pick "No household". A distinct token, not an empty string, so that a
#: removal is always a deliberate selection and can never be a field the browser left blank.
NO_HOUSEHOLD = "none"


def _form_value(form, key):
    return (form.get(key, [""])[0] or "").strip()


def _household_choices(connection, limit: int = 200):
    """Households a staff member can assign to, newest first. Presentation only — the id that
    comes back is validated against the table again inside the write transaction."""
    rows = connection.execute(
        select(households.c.id, households.c.name).order_by(households.c.id.desc()).limit(limit)
    ).mappings().all()
    return [{"id": r["id"], "name": r["name"]} for r in rows]


def _with_current_household(connection, choices, household_id):
    """Guarantee the person's own household is in the list.

    The list is capped, so an older household can fall off the end. Were that to happen the
    browser would fall back to the first option — "No household" — and a staff member who only
    fixed a typo would silently drop the client out of their family group on save. The current
    household is therefore always present and always the selected option.
    """
    if household_id is None or any(c["id"] == household_id for c in choices):
        return choices
    row = connection.execute(
        select(households.c.id, households.c.name).where(households.c.id == household_id)
    ).mappings().one_or_none()
    if row is None:
        return choices
    return [{"id": row["id"], "name": row["name"]}] + choices


def _render_form(request, person, *, errors, status_code=200):
    with engine.connect() as connection:
        choices = _with_current_household(connection, _household_choices(connection),
                                          person.get("household_id"))
        sharing = people_sharing_email(person.get("primary_email"),
                                       exclude_person_id=person["id"], conn=connection)
    return templates.TemplateResponse(
        request=request, name="people/edit.html", status_code=status_code,
        context={"person": person, "household_choices": choices, "sharing": sharing,
                 "errors": errors, "no_household": NO_HOUSEHOLD},
    )


@router.get("/people/{person_id}/edit", response_class=HTMLResponse)
def edit_person_form(request: Request, person_id: int,
                     principal: Principal = Depends(current_principal)):
    with engine.connect() as connection:
        person = connection.execute(
            select(people).where(people.c.id == person_id)
        ).mappings().one_or_none()
    if person is None:
        return render_error(request, 404, detail="Person not found.")
    # Sharing is shown before the edit, not only after saving: staff should know the address is
    # shared while they are deciding, rather than discovering it from a warning afterwards.
    return _render_form(request, dict(person), errors=[])


@router.post("/people/{person_id}/edit")
async def edit_person_submit(request: Request, person_id: int,
                             principal: Principal = Depends(current_principal)):
    form = parse_qs((await request.body()).decode("utf-8"))
    updates = {field: form[field][0] for field in EDITABLE_FIELDS if field in form}
    # A field the submission does not carry is left alone. Only the presence of the input means
    # "set this"; an absent ``birth_date`` is a form that does not edit dates, not an instruction
    # to erase the one on file.
    sent_name = "full_name" in form
    sent_birth_date = "birth_date" in form
    full_name = _form_value(form, "full_name")
    birth_date = _form_value(form, "birth_date")
    household_raw = _form_value(form, "household_id")
    errors: list[str] = []

    with engine.connect() as connection:
        current = connection.execute(
            select(people).where(people.c.id == person_id)
        ).mappings().one_or_none()
    if current is None:
        return render_error(request, 404, detail="Person not found.")

    request_id = getattr(request.state, "request_id", None)
    actor = principal.user_id

    # Validation happens BEFORE the transaction opens, so the common mistakes come back as a
    # readable form rather than as a rolled-back write.
    parsed_birth_date = current["birth_date"]
    if sent_birth_date:
        try:
            parsed_birth_date = _parse_birth_date(birth_date)
        except ValueError as exc:
            errors.append(str(exc))
            parsed_birth_date = current["birth_date"]

    if sent_name and not full_name:
        errors.append("Legal name cannot be blank.")

    # Three distinct states, kept distinct: leave the household alone, remove it, or move to the
    # household whose id was chosen. A blank select is the first of those and writes nothing.
    household_target = current["household_id"]
    if household_raw == NO_HOUSEHOLD:
        household_target = None
    elif household_raw:
        try:
            household_target = int(household_raw)
        except ValueError:
            errors.append("Household selection was not recognised.")

    if errors:
        # Nothing has been written at this point, and the rendered form says so.
        merged = dict(current)
        merged.update({k: v for k, v in updates.items() if v is not None})
        merged["full_name"] = full_name or current["full_name"]
        merged["birth_date"] = current["birth_date"]
        return _render_form(request, merged, errors=errors, status_code=400)

    changed: list[str] = []
    try:
        with engine.begin() as connection:
            changed += update_person_contact(person_id, updates, actor_user_id=actor,
                                             request_id=request_id, conn=connection)
            if sent_name and full_name != (current["full_name"] or ""):
                changed += correct_person_identity(person_id, full_name=full_name,
                                                   actor_user_id=actor, request_id=request_id,
                                                   conn=connection)
            if parsed_birth_date != current["birth_date"]:
                changed += correct_person_birth_date(person_id, birth_date=parsed_birth_date,
                                                     actor_user_id=actor, request_id=request_id,
                                                     conn=connection)
            if household_target != current["household_id"]:
                changed += set_person_household(person_id, household_target, actor_user_id=actor,
                                                request_id=request_id, conn=connection)
    except ValueError as exc:
        if "person not found" in str(exc).lower():
            return render_error(request, 404, detail=str(exc))
        return _render_form(request, dict(current), errors=[str(exc)], status_code=400)

    return RedirectResponse(f"/people/{person_id}?saved=1", status_code=303)
