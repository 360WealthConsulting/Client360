"""Enterprise Operational Resilience, Incident Management & Service Continuity Intelligence routes
(Phase D.60).

A governed COMPOSITION surface over the platform's authoritative operational-resilience owners — the
Observability service catalog / health / incidents / alerts, Security incidents, the Integration Platform,
Vendor Management, Automation Orchestration, and Business Continuity. Reads only — no second
incident-management platform / ticketing system / monitoring platform / help desk / DR platform /
change-management platform / CMDB / scheduler / alerting engine, no mutation. Routes are gated by
``observability.view`` OR ``analytics.executive``; each panel additionally self-restricts to its
authoritative-source capability. Diagnostics is gated by ``observability.audit``. No sensitive operational
payloads are ever returned — counts, status, and coverage only. Operational posture is never a certification
that production is healthy or continuity assured.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_any_capability
from app.security.models import Principal
from app.services.operational_resilience import (
    compose_dashboard,
    get_panel,
    list_dashboards,
    resilience_summary,
)
from app.services.operational_resilience.diagnostics import resilience_diagnostics
from app.services.operational_resilience.metrics import operational_resilience_metrics

router = APIRouter(tags=["operational-resilience"])
templates = Jinja2Templates(directory="app/templates")

# Operational resilience is an operations / executive surface — either capability may open it.
_OR_GATE = require_any_capability("observability.view", "analytics.executive")


@router.get("/operational-resilience", response_class=HTMLResponse)
def resilience_home(request: Request, dashboard: str | None = None,
                    principal: Principal = Depends(_OR_GATE)):
    """The operational-resilience dashboard (HTML). Renders the requested dashboard, or the first available."""
    accessible = list_dashboards(principal).get("dashboards", [])
    keys = [d["key"] for d in accessible]
    chosen = dashboard if dashboard in keys else (keys[0] if keys else None)
    result = compose_dashboard(principal, chosen) if chosen else None
    return templates.TemplateResponse(request=request, name="operational_resilience/home.html",
                                      context={"result": result, "dashboards": accessible, "chosen": chosen,
                                               "summary": resilience_summary(principal)})


@router.get("/api/v1/operational-resilience/dashboards")
def api_resilience_dashboards(principal: Principal = Depends(_OR_GATE)):
    """The resilience dashboards the principal may open (JSON, metadata only)."""
    return JSONResponse(list_dashboards(principal))


@router.get("/api/v1/operational-resilience/dashboard/{key}")
def api_resilience_dashboard(key: str, principal: Principal = Depends(_OR_GATE)):
    """Compose a named resilience dashboard (JSON). 404 when not registered or the principal lacks its
    required capability."""
    result = compose_dashboard(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/operational-resilience/summary")
def api_resilience_summary(principal: Principal = Depends(_OR_GATE)):
    """The firm operational-resilience summary (JSON) — compact, non-leaking. Backs the workspace panel + the
    Client 360 / Household 360 sections + AI grounding."""
    return JSONResponse(resilience_summary(principal))


@router.get("/api/v1/operational-resilience/registry")
def api_resilience_registry(principal: Principal = Depends(_OR_GATE)):
    """The operational-service + incident + continuity + recovery + dependency + panel + dashboard registries
    (JSON) — the declarative catalogs."""
    from app.services.operational_resilience import registry

    def _entries(reg):
        return [{"key": e.key, "label": e.label, "owner": e.owner, "runtime_gate": e.runtime_gate,
                 "capabilities": list(e.capabilities), "deep_links": list(e.deep_links),
                 "config_status": e.config_status} for e in reg]
    return JSONResponse({
        "operational_services": _entries(registry.OPERATIONAL_SERVICE_REGISTRY),
        "incident_categories": _entries(registry.INCIDENT_CATEGORY_REGISTRY),
        "continuity_capabilities": _entries(registry.CONTINUITY_CAPABILITY_REGISTRY),
        "recovery_objectives": _entries(registry.RECOVERY_OBJECTIVE_REGISTRY),
        "operational_dependencies": _entries(registry.OPERATIONAL_DEPENDENCY_REGISTRY),
        "panels": [{"key": p.key, "owner": p.owner, "source": p.source, "measure": p.measure,
                    "permission": p.permission, "deep_link": p.deep_link, "derived": p.derived,
                    "explainability": p.explainability} for p in registry.PANEL_REGISTRY],
        "dashboards": [{"key": d.key, "audience": d.audience, "runtime_gate": d.runtime_gate,
                        "panels": list(d.panels), "required_capabilities": list(d.required_capabilities),
                        "navigation": d.navigation, "governing_services": list(d.governing_services)}
                       for d in registry.RESILIENCE_DASHBOARDS],
        "coverage": registry.coverage()})


@router.get("/api/v1/operational-resilience/panel/{key}")
def api_resilience_panel(key: str, principal: Principal = Depends(_OR_GATE)):
    """Compose a single resilience panel (JSON). 404 when not registered."""
    result = get_panel(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/operational-resilience/metrics")
def api_resilience_metrics(principal: Principal = Depends(_OR_GATE)):
    """Low-cardinality operational-resilience-layer metrics (JSON)."""
    return JSONResponse(operational_resilience_metrics(principal))


@router.get("/operational-resilience/diagnostics")
def resilience_diag(principal: Principal = Depends(require_any_capability("observability.audit"))):
    """Internal-only operational-resilience diagnostics (registry coverage, panel availability, governance)."""
    return JSONResponse(resilience_diagnostics())
