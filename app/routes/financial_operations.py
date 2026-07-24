"""Enterprise Financial Operations, Revenue Intelligence & Firm Performance Governance routes (Phase D.57).

A governed COMPOSITION surface over the platform's authoritative financial owners — the insurance commission
ledger (`insurance_commissions` / `insurance_reporting`), the portfolio AUM owner, the single Analytics
Registry revenue metrics, Executive Reporting, and Practice Management. Reads only — no second accounting
platform / ERP / billing engine / commission engine / payroll system / bookkeeping platform / general ledger /
budgeting application, no mutation. Routes are gated by ``analytics.view``; each panel additionally
self-restricts to its own capability (firm financial figures require ``analytics.executive``). Diagnostics is
gated by ``observability.audit``. No payroll details, tax returns, bank account numbers, payment credentials,
or accounting payloads are ever returned — firm-level aggregate totals + status only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.financial_operations import (
    compose_dashboard,
    firm_financial_summary,
    get_panel,
    list_dashboards,
)
from app.services.financial_operations.diagnostics import financial_diagnostics
from app.services.financial_operations.metrics import financial_operations_metrics

router = APIRouter(tags=["financial-operations"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/financial-operations", response_class=HTMLResponse)
def financial_home(request: Request, dashboard: str | None = None,
                   principal: Principal = Depends(require_capability("analytics.view"))):
    """The financial-operations dashboard (HTML). Renders the requested dashboard, or the first available."""
    accessible = list_dashboards(principal).get("dashboards", [])
    keys = [d["key"] for d in accessible]
    chosen = dashboard if dashboard in keys else (keys[0] if keys else None)
    result = compose_dashboard(principal, chosen) if chosen else None
    return templates.TemplateResponse(request=request, name="financial_operations/home.html",
                                      context={"result": result, "dashboards": accessible, "chosen": chosen,
                                               "summary": firm_financial_summary(principal)})


@router.get("/api/v1/financial-operations/dashboards")
def api_financial_dashboards(principal: Principal = Depends(require_capability("analytics.view"))):
    """The financial dashboards the principal may open (JSON, metadata only)."""
    return JSONResponse(list_dashboards(principal))


@router.get("/api/v1/financial-operations/dashboard/{key}")
def api_financial_dashboard(key: str, principal: Principal = Depends(require_capability("analytics.view"))):
    """Compose a named financial dashboard (JSON). 404 when not registered or the principal lacks its required
    capability."""
    result = compose_dashboard(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/financial-operations/summary")
def api_financial_summary(principal: Principal = Depends(require_capability("analytics.view"))):
    """The firm financial-performance summary (JSON) — compact, non-leaking. Backs the workspace panel + the
    Client 360 / Household 360 sections + AI grounding."""
    return JSONResponse(firm_financial_summary(principal))


@router.get("/api/v1/financial-operations/registry")
def api_financial_registry(principal: Principal = Depends(require_capability("analytics.view"))):
    """The financial + revenue + panel + dashboard registries (JSON) — the declarative catalogs."""
    from app.services.financial_operations import registry
    return JSONResponse({
        "financial_categories": [{"key": f.key, "label": f.label,
                                  "authoritative_owner": f.authoritative_owner,
                                  "reporting_owner": f.reporting_owner,
                                  "calculation_owner": f.calculation_owner, "runtime_gate": f.runtime_gate,
                                  "deep_links": list(f.deep_links)}
                                 for f in registry.FINANCIAL_REGISTRY],
        "revenue_types": [{"key": r.key, "label": r.label, "category": r.category,
                           "authoritative_owner": r.authoritative_owner, "reporting_owner": r.reporting_owner,
                           "recognition_owner": r.recognition_owner, "runtime_gate": r.runtime_gate}
                          for r in registry.REVENUE_REGISTRY],
        "panels": [{"key": p.key, "owner": p.owner, "source": p.source, "measure": p.measure,
                    "permission": p.permission, "deep_link": p.deep_link, "explainability": p.explainability}
                   for p in registry.PANEL_REGISTRY],
        "dashboards": [{"key": d.key, "audience": d.audience, "runtime_gate": d.runtime_gate,
                        "panels": list(d.panels), "required_capabilities": list(d.required_capabilities),
                        "navigation": d.navigation, "governing_services": list(d.governing_services)}
                       for d in registry.FINANCIAL_DASHBOARDS],
        "coverage": registry.coverage()})


@router.get("/api/v1/financial-operations/panel/{key}")
def api_financial_panel(key: str, principal: Principal = Depends(require_capability("analytics.view"))):
    """Compose a single financial panel (JSON). 404 when not registered."""
    result = get_panel(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/financial-operations/metrics")
def api_financial_metrics(principal: Principal = Depends(require_capability("analytics.view"))):
    """Low-cardinality financial-operations-layer metrics (JSON)."""
    return JSONResponse(financial_operations_metrics(principal))


@router.get("/financial-operations/diagnostics")
def financial_diag(principal: Principal = Depends(require_capability("observability.audit"))):
    """Internal-only financial-operations diagnostics (registry coverage, panel availability, governance)."""
    return JSONResponse(financial_diagnostics())
