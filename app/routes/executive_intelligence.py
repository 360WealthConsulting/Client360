"""Enterprise Reporting & Executive Intelligence routes (Phase D.48).

A governed COMPOSITION surface over the authoritative operational services + the SINGLE Analytics Registry —
no second analytics/BI/warehouse/reporting-database. Reads only. Routes are gated by ``analytics.view``;
each dashboard's own required capability is enforced by the engine (executive dashboards need
``analytics.executive`` — a non-executive gets 404 for those, and executive widgets self-restrict).
Diagnostics is gated by ``observability.audit``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.executive_intelligence import (
    compose_dashboard,
    executive_summary,
    get_widget,
    list_dashboards,
)
from app.services.executive_intelligence.diagnostics import reporting_diagnostics
from app.services.executive_intelligence.metrics import reporting_metrics

router = APIRouter(tags=["executive-intelligence"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/executive", response_class=HTMLResponse)
def executive_home(request: Request, dashboard: str | None = None,
                   principal: Principal = Depends(require_capability("analytics.view"))):
    """The executive dashboard (HTML). Renders the requested dashboard, or the first the principal may see."""
    accessible = list_dashboards(principal).get("dashboards", [])
    keys = [d["key"] for d in accessible]
    chosen = dashboard if dashboard in keys else (keys[0] if keys else None)
    result = compose_dashboard(principal, chosen) if chosen else None
    return templates.TemplateResponse(request=request, name="executive_intelligence/home.html",
                                      context={"result": result, "dashboards": accessible, "chosen": chosen})


@router.get("/api/v1/executive/dashboards")
def api_executive_dashboards(principal: Principal = Depends(require_capability("analytics.view"))):
    """The dashboards the principal may open (JSON, metadata only)."""
    return JSONResponse(list_dashboards(principal))


@router.get("/api/v1/executive/dashboard/{key}")
def api_executive_dashboard(key: str, principal: Principal = Depends(require_capability("analytics.view"))):
    """Compose a named executive dashboard (JSON). 404 when not registered or the principal lacks its
    required capability."""
    result = compose_dashboard(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/executive/summary")
def api_executive_summary(principal: Principal = Depends(require_capability("analytics.view"))):
    """The firm executive summary (JSON) — composed executive dashboard + firm-intelligence observations.
    A non-executive receives a restricted, non-leaking envelope. Backs the sections + AI grounding."""
    return JSONResponse(executive_summary(principal))


@router.get("/api/v1/executive/widget/{key}")
def api_executive_widget(key: str, principal: Principal = Depends(require_capability("analytics.view"))):
    """Compose a single widget (JSON). 404 when not registered."""
    result = get_widget(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/executive/registry")
def api_executive_registry(principal: Principal = Depends(require_capability("analytics.view"))):
    """The dashboard + widget registries (JSON) — the declarative catalogs."""
    from app.services.executive_intelligence import registry
    return JSONResponse({
        "dashboards": [{"key": d.key, "audience": d.audience, "runtime_gate": d.runtime_gate,
                        "widgets": list(d.widgets), "required_capabilities": list(d.required_capabilities),
                        "navigation": d.navigation, "refresh_policy": d.refresh_policy,
                        "governing_services": list(d.governing_services)}
                       for d in registry.DASHBOARD_REGISTRY],
        "widgets": [{"key": w.key, "owner": w.owner, "source": w.source, "aggregation": w.aggregation,
                     "permission": w.permission, "deep_link": w.deep_link, "explainability": w.explainability}
                    for w in registry.WIDGET_REGISTRY],
        "coverage": registry.coverage()})


@router.get("/api/v1/executive/metrics")
def api_executive_metrics(principal: Principal = Depends(require_capability("analytics.view"))):
    """Low-cardinality reporting-layer metrics (JSON)."""
    return JSONResponse(reporting_metrics(principal))


@router.get("/executive/diagnostics")
def executive_diag(principal: Principal = Depends(require_capability("observability.audit"))):
    """Internal-only reporting diagnostics (registry coverage, widget availability, governance)."""
    return JSONResponse(reporting_diagnostics())
