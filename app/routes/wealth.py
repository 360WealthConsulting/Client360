from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.portfolio import get_wealth_dashboard

router = APIRouter(prefix="/wealth", tags=["wealth"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
def wealth_dashboard(
    request: Request,
    principal: Principal = Depends(require_capability("client.read")),
):
    """Advisor-facing firm-wide wealth overview.

    A read-only landing page for the WEALTH section. It reuses the existing
    portfolio services (`get_wealth_dashboard` → `get_firm_portfolio_metrics`) —
    no new aggregation, schema, or policy. Middleware additionally enforces
    `record.read_all` (FIRM_WIDE_COLLECTION), so this is gated exactly like
    `/portfolio`.
    """
    dashboard = get_wealth_dashboard()
    return templates.TemplateResponse(
        request=request,
        name="wealth/dashboard.html",
        context={"principal": principal, "d": dashboard},
    )
