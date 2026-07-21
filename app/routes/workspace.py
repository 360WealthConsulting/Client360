from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.security.authorization import record_in_scope
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.advisor_workspace import get_daily_dashboard, get_meeting_brief

router = APIRouter(prefix="/workspace", tags=["workspace"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
def workspace_dashboard(
    request: Request,
    principal: Principal = Depends(require_capability("client.read")),
):
    """Advisor Workspace — read-only Daily Dashboard (Phase D.1).

    Book-scoped (NOT a firm-wide collection): the route requires `client.read`
    and the orchestration service scopes every panel to the advisor's accessible
    clients via `accessible_person_ids`. Composition only — no writes, no new
    domain/task/workflow/exception/notification logic, no advisor intelligence.
    """
    dashboard = get_daily_dashboard(principal)
    return templates.TemplateResponse(
        request=request,
        name="workspace/dashboard.html",
        context={"principal": principal, "d": dashboard},
    )


@router.get("/meetings/{person_id}", response_class=HTMLResponse)
def meeting_brief(
    request: Request,
    person_id: int,
    event: int | None = None,
    principal: Principal = Depends(require_capability("client.read")),
):
    """Meeting Workspace — read-only meeting-preparation brief for one client
    (Phase D.3). `/workspace/meetings/{id}` is NOT covered by the middleware
    RECORD_PATH, so this route enforces person record-scope explicitly (404 for an
    inaccessible person, matching the person-profile behavior). The optional
    `event` id is validated in the service to belong to this person and to be a
    calendar event; otherwise a general brief is rendered.
    """
    if not record_in_scope(principal, "person", person_id):
        raise HTTPException(404, "Not found")
    brief = get_meeting_brief(person_id, event_id=event)
    if brief is None:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(
        request=request,
        name="workspace/meeting_brief.html",
        context={"principal": principal, "b": brief},
    )
