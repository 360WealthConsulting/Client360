"""Client 360 Workspace routes (Phase D.40).

`GET /client/{id}` is the master client record — a read-only COMPOSITION of the authoritative domain
services for one person or household. The workspace never mutates: every quick action deep-links into
the authoritative create workflow. Record scope is verified inside `get_workspace` (returns None → 404);
the page is gated by `client.read`. `/client/.../diagnostics` reuses `observability.audit`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from app.db import engine
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.client360 import get_workspace
from app.services.client360.common import as_json
from app.services.client360.diagnostics import client360_diagnostics
from app.templating import render_error

router = APIRouter(prefix="/client", tags=["client360"])
templates = Jinja2Templates(directory="app/templates")



def _drake_returns_for_person(person_id: int) -> list[dict]:
    """Read-only Drake lookup using the Client360 person's canonical name."""
    query = text("""
        SELECT
            d.id,
            d.tax_year,
            d.return_type,
            d.agi,
            d.preparer_code,
            d.filing_status,
            d.federal_product,
            d.federal_ack_date,
            d.federal_ack_code,
            d.state_product,
            d.state_ack_date,
            d.state_ack_code,
            d.taxpayer_first_name,
            d.taxpayer_last_name,
            d.spouse_first_name,
            d.spouse_last_name
        FROM people p
        JOIN drake_client_returns d
          ON (
               lower(trim(d.taxpayer_first_name)) = lower(trim(p.first_name))
               AND lower(trim(d.taxpayer_last_name)) = lower(trim(p.last_name))
             )
          OR (
               lower(trim(d.spouse_first_name)) = lower(trim(p.first_name))
               AND lower(trim(d.taxpayer_last_name)) = lower(trim(p.last_name))
             )
        WHERE p.id = :person_id
        ORDER BY d.tax_year DESC, d.id DESC
    """)

    with engine.connect() as connection:
        # Deployment-order tolerance: the Drake schema is provisioned separately and may not exist in
        # every environment (e.g. release before the Drake migration is applied). Degrade to no Drake
        # returns rather than 500 the entire client workspace.
        if connection.execute(text("SELECT to_regclass('public.drake_client_returns')")).scalar() is None:
            return []
        rows = connection.execute(
            query,
            {"person_id": person_id},
        ).mappings().all()

    return [dict(row) for row in rows]


def _render(request, ws, principal, tab):
    tabs = [s for s in ws["section_keys"]]
    active = tab if tab in tabs else (tabs[0] if tabs else "summary")
    return templates.TemplateResponse(request=request, name="client360/workspace.html", context={
        "principal": principal, "ws": ws, "active_tab": active})


@router.get("/household/{household_id}", response_class=HTMLResponse)
def household_workspace(request: Request, household_id: int, tab: str = "summary",
                        principal: Principal = Depends(require_capability("client.read"))):
    """Household 360 Workspace (Phase D.41) — the authoritative household surface at the D.40 route.
    Read-only composition of member-level rollups; every edit deep-links into the domain workflow."""
    from app.services.client360.household import get_household_workspace
    ws = get_household_workspace(principal, household_id)
    if ws is None:
        return render_error(request, 404, detail="Household not found.")
    tabs = ws["section_keys"]
    active = tab if tab in tabs else (tabs[0] if tabs else "summary")
    return templates.TemplateResponse(request=request, name="client360/household.html", context={
        "principal": principal, "ws": ws, "active_tab": active})


@router.get("/household/{household_id}/snapshot")
def household_snapshot(household_id: int,
                       principal: Principal = Depends(require_capability("client.read"))):
    """AI-ready compact household snapshot (JSON). 404 if out of record scope; same security as the page."""
    from fastapi import HTTPException

    from app.services.client360.household import get_household_workspace
    ws = get_household_workspace(principal, household_id)
    if ws is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(as_json(ws["snapshot"]))


@router.get("/household/{household_id}/diagnostics")
def household_diagnostics_route(
        household_id: int, principal: Principal = Depends(require_capability("observability.audit"))):
    """Household 360 composition diagnostics + governance (JSON)."""
    from app.services.client360.diagnostics import household_diagnostics
    from app.services.client360.governance import validate_household360
    return JSONResponse(as_json({"diagnostics": household_diagnostics(principal, household_id=household_id),
                                 "governance": validate_household360(principal)}))


@router.post("/documents/resolve")
def resolve_document_ownership(
        request: Request, folder: str = Form(...), household_id: int | None = Form(None),
        person_id: int | None = Form(None), return_to: str = Form("/"),
        principal: Principal = Depends(require_capability("client.write"))):
    """In-product Resolve Ownership: link an unassigned TaxDome folder's documents to an existing
    household or person. Reuses the household ownership service (audited, fills NULLs only — no new
    ownership logic, no duplicate rows). This replaces the PowerShell repair for staff."""
    from app.security.authorization import record_in_scope
    from app.services.households import resolve_folder_ownership
    if household_id is not None and not record_in_scope(principal, "household", household_id):
        return render_error(request, 404, detail="Household not found.")
    if person_id is not None and not record_in_scope(principal, "person", person_id):
        return render_error(request, 404, detail="Client not found.")
    try:
        resolve_folder_ownership(folder, household_id=household_id, person_id=person_id,
                                 actor_user_id=principal.user_id,
                                 request_id=getattr(request.state, "request_id", None))
    except ValueError as exc:
        return render_error(request, 400, detail=str(exc))
    return RedirectResponse(return_to or "/", status_code=303)


def _rid(request):
    return getattr(request.state, "request_id", None)


@router.post("/{person_id}/tasks")
def create_client_task(request: Request, person_id: int, title: str = Form(...),
                       priority: str = Form("normal"), due_date: str = Form(""),
                       principal: Principal = Depends(require_capability("client.write"))):
    """Create a client task in-workspace (reuses app.services.tasks.create_task — audited)."""
    from datetime import date as _date

    from app.security.authorization import record_in_scope
    from app.services.tasks import create_task
    if not record_in_scope(principal, "person", person_id):
        return render_error(request, 404, detail="Client not found.")
    due = None
    if due_date.strip():
        try:
            due = _date.fromisoformat(due_date.strip())
        except ValueError:
            return render_error(request, 400, detail="Due date must be YYYY-MM-DD.")
    try:
        create_task(person_id, title=title, priority=priority, due_date=due,
                    actor_user_id=principal.user_id, request_id=_rid(request), source="client360")
    except ValueError as exc:
        return render_error(request, 400, detail=str(exc))
    return RedirectResponse(f"/client/{person_id}?tab=tasks", status_code=303)


@router.post("/{person_id}/tasks/{task_id}/complete")
def complete_client_task(request: Request, person_id: int, task_id: int,
                         principal: Principal = Depends(require_capability("client.write"))):
    """Mark a client task complete in-workspace (reuses app.services.tasks.complete_task — audited)."""
    from app.security.authorization import record_in_scope
    from app.services.tasks import complete_task
    if not record_in_scope(principal, "person", person_id):
        return render_error(request, 404, detail="Client not found.")
    complete_task(person_id, task_id, actor_user_id=principal.user_id, request_id=_rid(request))
    return RedirectResponse(f"/client/{person_id}?tab=tasks", status_code=303)


@router.post("/{person_id}/notes")
def add_client_note(request: Request, person_id: int, body: str = Form(...),
                    note_type: str = Form("note"),
                    principal: Principal = Depends(require_capability("client.write"))):
    """Add an internal client note in-workspace (reuses app.services.notes.add_person_note; audited
    here). Internal-only — person notes are never exposed to the client portal by this action."""
    from app.security.audit import write_audit_event
    from app.security.authorization import record_in_scope
    from app.services.notes import ACTIVITY_NOTE_TYPES, add_person_note
    if not record_in_scope(principal, "person", person_id):
        return render_error(request, 404, detail="Client not found.")
    if note_type not in ACTIVITY_NOTE_TYPES:
        note_type = "note"
    try:
        note_id = add_person_note(person_id, body, author_user_id=principal.user_id, note_type=note_type)
    except ValueError as exc:
        return render_error(request, 400, detail=str(exc))
    write_audit_event(action="note.created", entity_type="person", entity_id=person_id,
                      actor_user_id=principal.user_id, request_id=_rid(request),
                      metadata={"note_id": note_id, "note_type": note_type})
    return RedirectResponse(f"/client/{person_id}?tab=notes", status_code=303)


@router.get("/{person_id}", response_class=HTMLResponse)
def client_workspace(request: Request, person_id: int, tab: str = "summary",
                     q: str | None = None, category: str | None = None,
                     document_type: str | None = None, status: str | None = None,
                     year: int | None = None, doc: int | None = None,
                     principal: Principal = Depends(require_capability("client.read"))):
    # Vault-tab UI state (filters + selected document) threaded into the composition context.
    vault_view = {"q": q, "category": category, "document_type": document_type,
                  "status": status, "year": year, "doc": doc}
    ws = get_workspace(principal, person_id=person_id, vault_view=vault_view)
    if ws is None:
        return render_error(request, 404, detail="Client not found.")
    ws["drake_returns"] = _drake_returns_for_person(person_id)
    return _render(request, ws, principal, tab)


@router.get("/{person_id}/snapshot")
def client_snapshot(person_id: int,
                    principal: Principal = Depends(require_capability("client.read"))):
    """AI-ready compact client snapshot (JSON). 404 if out of record scope."""
    from fastapi import HTTPException

    from app.security.authorization import record_in_scope
    if not record_in_scope(principal, "person", person_id):
        raise HTTPException(404, "Not found")
    ws = get_workspace(principal, person_id=person_id, section_timings=False)
    if ws is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(as_json(ws["snapshot"]))


@router.get("/{person_id}/diagnostics")
def client_diagnostics(person_id: int,
                       principal: Principal = Depends(require_capability("observability.audit"))):
    """Client 360 composition diagnostics + governance (JSON). Reuses observability.audit."""
    from app.services.client360.governance import validate_client360
    return JSONResponse(as_json({"diagnostics": client360_diagnostics(principal, person_id=person_id),
                                 "governance": validate_client360(principal)}))
