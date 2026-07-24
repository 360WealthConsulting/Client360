"""Enterprise Practice Management, Capacity Planning & Resource Operations routes (Phase D.49).

A governed COMPOSITION surface over the platform's authoritative operational owners — Operations Capacity
(the capacity/utilization owner), the Unified Work Queue, Workflow Automation, Operational + Compliance
Intelligence, the opportunity + Analytics firm-intelligence layers, and the tax domain. Reads only — no
second workflow/scheduler/staffing/queue/planning engine, no mutation. Routes are gated by ``capacity.read``
(the practice-management capability); each panel additionally self-restricts to its own capability.
Diagnostics is gated by ``observability.audit``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.practice_management import (
    compose_dashboard,
    get_panel,
    list_dashboards,
    practice_summary,
)
from app.services.practice_management.diagnostics import practice_diagnostics
from app.services.practice_management.metrics import practice_metrics

router = APIRouter(tags=["practice-management"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/practice", response_class=HTMLResponse)
def practice_home(request: Request, dashboard: str | None = None,
                  principal: Principal = Depends(require_capability("capacity.read"))):
    """The practice-management dashboard (HTML). Renders the requested dashboard, or the first available."""
    accessible = list_dashboards(principal).get("dashboards", [])
    keys = [d["key"] for d in accessible]
    chosen = dashboard if dashboard in keys else (keys[0] if keys else None)
    result = compose_dashboard(principal, chosen) if chosen else None
    return templates.TemplateResponse(request=request, name="practice_management/home.html",
                                      context={"result": result, "dashboards": accessible, "chosen": chosen,
                                               "summary": practice_summary(principal)})


@router.get("/api/v1/practice/dashboards")
def api_practice_dashboards(principal: Principal = Depends(require_capability("capacity.read"))):
    """The practice dashboards the principal may open (JSON, metadata only)."""
    return JSONResponse(list_dashboards(principal))


@router.get("/api/v1/practice/dashboard/{key}")
def api_practice_dashboard(key: str, principal: Principal = Depends(require_capability("capacity.read"))):
    """Compose a named practice dashboard (JSON). 404 when not registered or the principal lacks its
    required capability."""
    result = compose_dashboard(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/practice/summary")
def api_practice_summary(principal: Principal = Depends(require_capability("capacity.read"))):
    """The firm practice-management summary (JSON) — compact, non-leaking. Backs the workspace panel + the
    Client 360 / Household 360 sections + AI grounding."""
    return JSONResponse(practice_summary(principal))


@router.get("/api/v1/practice/registry")
def api_practice_registry(principal: Principal = Depends(require_capability("capacity.read"))):
    """The capacity + resource + panel + dashboard registries (JSON) — the declarative catalogs."""
    from app.services.practice_management import registry
    return JSONResponse({
        "capacity_models": [{"key": c.key, "label": c.label, "owner": c.owner,
                             "governing_workflow": c.governing_workflow, "workload_source": c.workload_source,
                             "utilization_method": c.utilization_method,
                             "planning_horizon": c.planning_horizon, "runtime_gate": c.runtime_gate,
                             "refresh_policy": c.refresh_policy, "deep_links": list(c.deep_links)}
                            for c in registry.CAPACITY_REGISTRY],
        "resources": [{"key": r.key, "label": r.label, "owner": r.owner,
                       "capabilities": list(r.capabilities), "workload_source": r.workload_source,
                       "assignment_source": r.assignment_source, "scheduling_source": r.scheduling_source,
                       "utilization_source": r.utilization_source,
                       "availability_source": r.availability_source} for r in registry.RESOURCE_REGISTRY],
        "panels": [{"key": p.key, "owner": p.owner, "source": p.source, "measure": p.measure,
                    "permission": p.permission, "deep_link": p.deep_link,
                    "explainability": p.explainability} for p in registry.PANEL_REGISTRY],
        "dashboards": [{"key": d.key, "audience": d.audience, "runtime_gate": d.runtime_gate,
                        "panels": list(d.panels), "required_capabilities": list(d.required_capabilities),
                        "navigation": d.navigation, "governing_services": list(d.governing_services)}
                       for d in registry.PRACTICE_DASHBOARDS],
        "coverage": registry.coverage()})


@router.get("/api/v1/practice/panel/{key}")
def api_practice_panel(key: str, principal: Principal = Depends(require_capability("capacity.read"))):
    """Compose a single practice panel (JSON). 404 when not registered."""
    result = get_panel(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/practice/metrics")
def api_practice_metrics(principal: Principal = Depends(require_capability("capacity.read"))):
    """Low-cardinality practice-management-layer metrics (JSON)."""
    return JSONResponse(practice_metrics(principal))


@router.get("/practice/diagnostics")
def practice_diag(principal: Principal = Depends(require_capability("observability.audit"))):
    """Internal-only practice-management diagnostics (registry coverage, panel availability, governance)."""
    return JSONResponse(practice_diagnostics())
