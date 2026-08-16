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
    principal: Principal = Depends(require_capability("portfolio.firm_metrics")),
):
    """Firm-wide wealth overview — privileged firm financial aggregates.

    The whole page is firm-wide portfolio metrics (`get_wealth_dashboard` →
    `get_firm_portfolio_metrics`: firm AUM, cash, largest household/position). It is
    therefore gated on `portfolio.firm_metrics`, the dedicated firm-metrics capability —
    NOT `client.read`/`record.read_all` — so an employee who can read records does not
    automatically see firm AUM here. No new aggregation, schema, or business policy.
    """
    dashboard = get_wealth_dashboard()
    return templates.TemplateResponse(
        request=request,
        name="wealth/dashboard.html",
        context={"principal": principal, "d": dashboard},
    )
