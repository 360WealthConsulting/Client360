from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.advisor_workspace import get_daily_dashboard

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
