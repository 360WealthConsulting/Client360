"""Business/organization entity workspace (read-only).

``/business/{organization_id}`` opens the canonical business entity: name, owners/principals with
roles, associated people, related household(s), documents, and provenance — with navigation to each
person/household workspace and back. Gated by ``client.read``; the composition is a pure read over the
authoritative ownership graph and document ownership (no writes, no document reassignment).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.business_workspace import get_business_workspace
from app.templating import install_filters, render_error

router = APIRouter(tags=["business"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


@router.get("/business/{organization_id}", response_class=HTMLResponse)
def business_workspace(request: Request, organization_id: int,
                       principal: Principal = Depends(require_capability("client.read"))):
    ws = get_business_workspace(organization_id)
    if ws is None:
        return render_error(request, 404, detail="Business not found.")
    return templates.TemplateResponse(
        request=request, name="business/workspace.html",
        context={"principal": principal, "ws": ws})
