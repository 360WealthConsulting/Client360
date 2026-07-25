"""Enterprise Capacity Planning, Workforce Operations & Resource Intelligence routes (Phase D.61).

A governed COMPOSITION surface over the platform's authoritative workforce / capacity / utilization owners —
the Operations capacity owner, the Work Queue, Practice Management, and Automation Orchestration. Reads only —
no second HR platform / HCM / scheduling application / calendar system / project-management system / PSA /
time-tracking platform / payroll platform / workforce-management system, no mutation. Routes are gated by
``capacity.read`` OR ``analytics.executive``; each panel additionally self-restricts to its
authoritative-source capability. Diagnostics is gated by ``observability.audit``. No employee details,
payroll, HR records, calendar contents, time entries, or sensitive staffing data are ever returned — counts,
status, and coverage only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_any_capability
from app.security.models import Principal
from app.services.capacity_planning import (
    capacity_summary,
    compose_dashboard,
    get_panel,
    list_dashboards,
)
from app.services.capacity_planning.diagnostics import capacity_diagnostics
from app.services.capacity_planning.metrics import capacity_planning_metrics

router = APIRouter(tags=["capacity-planning"])
templates = Jinja2Templates(directory="app/templates")

# Capacity planning is an operations / executive surface — either capability may open it.
_CP_GATE = require_any_capability("capacity.read", "analytics.executive")


@router.get("/capacity-planning", response_class=HTMLResponse)
def capacity_home(request: Request, dashboard: str | None = None,
                  principal: Principal = Depends(_CP_GATE)):
    """The capacity-planning dashboard (HTML). Renders the requested dashboard, or the first available."""
    accessible = list_dashboards(principal).get("dashboards", [])
    keys = [d["key"] for d in accessible]
    chosen = dashboard if dashboard in keys else (keys[0] if keys else None)
    result = compose_dashboard(principal, chosen) if chosen else None
    return templates.TemplateResponse(request=request, name="capacity_planning/home.html",
                                      context={"result": result, "dashboards": accessible, "chosen": chosen,
                                               "summary": capacity_summary(principal)})


@router.get("/api/v1/capacity-planning/dashboards")
def api_capacity_dashboards(principal: Principal = Depends(_CP_GATE)):
    """The resource dashboards the principal may open (JSON, metadata only)."""
    return JSONResponse(list_dashboards(principal))


@router.get("/api/v1/capacity-planning/dashboard/{key}")
def api_capacity_dashboard(key: str, principal: Principal = Depends(_CP_GATE)):
    """Compose a named resource dashboard (JSON). 404 when not registered or the principal lacks its required
    capability."""
    result = compose_dashboard(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/capacity-planning/summary")
def api_capacity_summary(principal: Principal = Depends(_CP_GATE)):
    """The firm capacity & workload summary (JSON) — compact, non-leaking. Backs the workspace panel + the
    Client 360 / Household 360 sections + AI grounding."""
    return JSONResponse(capacity_summary(principal))


@router.get("/api/v1/capacity-planning/registry")
def api_capacity_registry(principal: Principal = Depends(_CP_GATE)):
    """The workforce + capacity + utilization + panel + dashboard registries (JSON) — the declarative
    catalogs."""
    from app.services.capacity_planning import registry

    def _entries(reg):
        return [{"key": e.key, "label": e.label, "owner": e.owner, "runtime_gate": e.runtime_gate,
                 "capabilities": list(e.capabilities), "deep_links": list(e.deep_links),
                 "config_status": e.config_status} for e in reg]
    return JSONResponse({
        "workforce_classes": _entries(registry.WORKFORCE_REGISTRY),
        "capacity_categories": _entries(registry.CAPACITY_REGISTRY),
        "utilization_categories": _entries(registry.UTILIZATION_REGISTRY),
        "panels": [{"key": p.key, "owner": p.owner, "source": p.source, "measure": p.measure,
                    "permission": p.permission, "deep_link": p.deep_link, "derived": p.derived,
                    "explainability": p.explainability} for p in registry.PANEL_REGISTRY],
        "dashboards": [{"key": d.key, "audience": d.audience, "runtime_gate": d.runtime_gate,
                        "panels": list(d.panels), "required_capabilities": list(d.required_capabilities),
                        "navigation": d.navigation, "governing_services": list(d.governing_services)}
                       for d in registry.RESOURCE_DASHBOARDS],
        "coverage": registry.coverage()})


@router.get("/api/v1/capacity-planning/panel/{key}")
def api_capacity_panel(key: str, principal: Principal = Depends(_CP_GATE)):
    """Compose a single resource panel (JSON). 404 when not registered."""
    result = get_panel(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/capacity-planning/metrics")
def api_capacity_metrics(principal: Principal = Depends(_CP_GATE)):
    """Low-cardinality capacity-planning-layer metrics (JSON)."""
    return JSONResponse(capacity_planning_metrics(principal))


@router.get("/capacity-planning/diagnostics")
def capacity_diag(principal: Principal = Depends(require_any_capability("observability.audit"))):
    """Internal-only capacity-planning diagnostics (registry coverage, panel availability, governance)."""
    return JSONResponse(capacity_diagnostics())
