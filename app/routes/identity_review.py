"""Match Review — unresolved single-source contacts (Sprint 2, BL-2).

promote_unlinked auto-promotes unambiguous single-source contacts on import but deliberately
leaves ambiguous ones (several candidate people, or a contact detail shared with another unlinked
contact) for a human. This queue surfaces those and lets staff resolve each by linking to an
existing person or creating a new one. Resolution is a human decision — no automatic merge
thresholds are applied. Every resolution is audited.
"""
import uuid
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.matching.promote import (
    list_ambiguous_unlinked,
    promote_unlinked,
    resolve_create_person,
    resolve_link_to_person,
)
from app.security.audit import write_audit_event
from app.security.dependencies import current_principal
from app.security.models import Principal
from app.templating import render_error

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or f"review-{uuid.uuid4()}"


@router.get("/matches/unresolved", response_class=HTMLResponse)
def unresolved_contacts(request: Request, principal: Principal = Depends(current_principal)):
    contacts = list_ambiguous_unlinked()
    return templates.TemplateResponse(
        request=request, name="matches/unresolved.html",
        context={
            "contacts": contacts,
            "saved": request.query_params.get("saved") == "1",
            "promoted": request.query_params.get("promoted"),
        },
    )


@router.post("/matches/promote-unlinked")
async def run_promotion_backfill(request: Request,
                                 principal: Principal = Depends(current_principal)):
    """One-time / on-demand backfill: promote every unlinked single-source contact (e.g. those
    imported before promotion was wired into the importer). Conservative — unique contacts become
    people, exact matches link, ambiguous cases stay for review."""
    report = promote_unlinked()
    write_audit_event(
        action="identity.promotion_backfill", entity_type="source_contact", entity_id="all",
        actor_user_id=principal.user_id, request_id=_request_id(request),
        metadata=report.to_dict(),
    )
    return RedirectResponse(
        f"/matches/unresolved?promoted={report.created}", status_code=303)


@router.post("/matches/unresolved/{source_contact_id}/resolve")
async def resolve_contact(request: Request, source_contact_id: int,
                          principal: Principal = Depends(current_principal)):
    form = parse_qs((await request.body()).decode("utf-8"))
    action = form.get("action", [""])[0]
    if action == "link":
        person_raw = form.get("person_id", [""])[0].strip()
        if not person_raw:
            return render_error(request, 400, detail="Choose a person to link to.")
        person_id = int(person_raw)
        resolve_link_to_person(source_contact_id, person_id)
    elif action == "create":
        person_id = resolve_create_person(source_contact_id)
    else:
        return render_error(request, 400, detail="Unknown resolution action.")
    write_audit_event(
        action="identity.contact_resolved", entity_type="source_contact",
        entity_id=source_contact_id, actor_user_id=principal.user_id,
        request_id=_request_id(request),
        metadata={"resolution": action, "person_id": person_id},
    )
    return RedirectResponse("/matches/unresolved?saved=1", status_code=303)
