"""Client 360 Workspace routes (Phase D.40).

`GET /client/{id}` is the master client record — a read-only COMPOSITION of the authoritative domain
services for one person or household. The workspace never mutates: every quick action deep-links into
the authoritative create workflow. Record scope is verified inside `get_workspace` (returns None → 404);
the page is gated by `client.read`. `/client/.../diagnostics` reuses `observability.audit`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.client360 import get_workspace
from app.services.client360.common import as_json
from app.services.client360.diagnostics import client360_diagnostics
from app.templating import render_error

router = APIRouter(prefix="/client", tags=["client360"])
templates = Jinja2Templates(directory="app/templates")


def _render(request, ws, principal, tab):
    tabs = [s for s in ws["section_keys"]]
    active = tab if tab in tabs else (tabs[0] if tabs else "summary")
    return templates.TemplateResponse(request=request, name="client360/workspace.html", context={
        "principal": principal, "ws": ws, "active_tab": active})


@router.get("/household/{household_id}", response_class=HTMLResponse)
def household_workspace(request: Request, household_id: int, tab: str = "summary",
                        principal: Principal = Depends(require_capability("client.read"))):
    ws = get_workspace(principal, household_id=household_id)
    if ws is None:
        return render_error(request, 404, detail="Client not found.")
    return _render(request, ws, principal, tab)


@router.get("/household/{household_id}/diagnostics")
def household_diagnostics(household_id: int,
                          principal: Principal = Depends(require_capability("observability.audit"))):
    return JSONResponse(as_json(client360_diagnostics(principal, household_id=household_id)))


@router.get("/{person_id}", response_class=HTMLResponse)
def client_workspace(request: Request, person_id: int, tab: str = "summary",
                     principal: Principal = Depends(require_capability("client.read"))):
    ws = get_workspace(principal, person_id=person_id)
    if ws is None:
        return render_error(request, 404, detail="Client not found.")
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
