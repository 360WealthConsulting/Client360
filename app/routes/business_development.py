"""Business Development dashboard (Phase D.14) — executive summary + pipeline attribution +
deterministic BD intelligence. Read-only composition over Campaign/Referral/Opportunity
reporting; gated by campaign.report (the broad business-development read capability)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.bizdev import intelligence
from app.templating import install_filters

router = APIRouter(prefix="/business-development", tags=["business-development"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request,
              principal: Principal = Depends(require_capability("campaign.report"))):
    summary = intelligence.executive_summary(principal)
    intel = intelligence.business_development_intelligence(principal)
    return templates.TemplateResponse(request=request, name="business_development/dashboard.html",
                                      context={"principal": principal, "s": summary, "intel": intel})
