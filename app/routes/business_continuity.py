"""Enterprise Business Continuity, Disaster Recovery & Operational Resilience routes (Phase D.55).

A governed COMPOSITION surface over the platform's authoritative operational-resilience owners — the
Observability domain, the Runtime engine, the Automation scheduler, and Communications. Reads only — no
second backup platform / monitoring system / DR engine / scheduler / notification system / incident manager,
no mutation, no backup or restore execution. Routes are gated by ``observability.view``; each panel
additionally self-restricts to its own capability. Diagnostics is gated by ``observability.audit``. No
infrastructure payloads or client-sensitive data are ever returned — counts + status only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.business_continuity import (
    compose_dashboard,
    continuity_summary,
    get_panel,
    list_dashboards,
)
from app.services.business_continuity.diagnostics import continuity_diagnostics
from app.services.business_continuity.metrics import business_continuity_metrics

router = APIRouter(tags=["business-continuity"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/business-continuity", response_class=HTMLResponse)
def continuity_home(request: Request, dashboard: str | None = None,
                    principal: Principal = Depends(require_capability("observability.view"))):
    """The business-continuity dashboard (HTML). Renders the requested dashboard, or the first available."""
    accessible = list_dashboards(principal).get("dashboards", [])
    keys = [d["key"] for d in accessible]
    chosen = dashboard if dashboard in keys else (keys[0] if keys else None)
    result = compose_dashboard(principal, chosen) if chosen else None
    return templates.TemplateResponse(request=request, name="business_continuity/home.html",
                                      context={"result": result, "dashboards": accessible, "chosen": chosen,
                                               "summary": continuity_summary(principal)})


@router.get("/api/v1/business-continuity/dashboards")
def api_continuity_dashboards(principal: Principal = Depends(require_capability("observability.view"))):
    """The continuity dashboards the principal may open (JSON, metadata only)."""
    return JSONResponse(list_dashboards(principal))


@router.get("/api/v1/business-continuity/dashboard/{key}")
def api_continuity_dashboard(key: str, principal: Principal = Depends(require_capability("observability.view"))):
    """Compose a named continuity dashboard (JSON). 404 when not registered or the principal lacks its
    required capability."""
    result = compose_dashboard(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/business-continuity/summary")
def api_continuity_summary(principal: Principal = Depends(require_capability("observability.view"))):
    """The firm operational-resilience summary (JSON) — compact, non-leaking. Backs the workspace panel + the
    Client 360 / Household 360 sections + AI grounding."""
    return JSONResponse(continuity_summary(principal))


@router.get("/api/v1/business-continuity/registry")
def api_continuity_registry(principal: Principal = Depends(require_capability("observability.view"))):
    """The resilience + recovery + panel + dashboard registries (JSON) — the declarative catalogs."""
    from app.services.business_continuity import registry
    return JSONResponse({
        "resilience_domains": [{"key": r.key, "label": r.label, "authoritative_owner": r.authoritative_owner,
                                "health_owner": r.health_owner, "monitoring_owner": r.monitoring_owner,
                                "runtime_gate": r.runtime_gate, "deep_links": list(r.deep_links)}
                               for r in registry.RESILIENCE_REGISTRY],
        "recovery_assets": [{"key": a.key, "label": a.label, "owner": a.owner, "backup_owner": a.backup_owner,
                             "restore_owner": a.restore_owner, "rpo": a.rpo, "rto": a.rto,
                             "runtime_gate": a.runtime_gate} for a in registry.RECOVERY_REGISTRY],
        "panels": [{"key": p.key, "owner": p.owner, "source": p.source, "measure": p.measure,
                    "permission": p.permission, "deep_link": p.deep_link, "explainability": p.explainability}
                   for p in registry.PANEL_REGISTRY],
        "dashboards": [{"key": d.key, "audience": d.audience, "runtime_gate": d.runtime_gate,
                        "panels": list(d.panels), "required_capabilities": list(d.required_capabilities),
                        "navigation": d.navigation, "governing_services": list(d.governing_services)}
                       for d in registry.CONTINUITY_DASHBOARDS],
        "coverage": registry.coverage()})


@router.get("/api/v1/business-continuity/panel/{key}")
def api_continuity_panel(key: str, principal: Principal = Depends(require_capability("observability.view"))):
    """Compose a single continuity panel (JSON). 404 when not registered."""
    result = get_panel(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/business-continuity/metrics")
def api_continuity_metrics(principal: Principal = Depends(require_capability("observability.view"))):
    """Low-cardinality business-continuity-layer metrics (JSON)."""
    return JSONResponse(business_continuity_metrics(principal))


@router.get("/business-continuity/diagnostics")
def continuity_diag(principal: Principal = Depends(require_capability("observability.audit"))):
    """Internal-only business-continuity diagnostics (registry coverage, panel availability, governance)."""
    return JSONResponse(continuity_diagnostics())
