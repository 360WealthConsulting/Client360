"""Enterprise Data Governance, Master Data & Platform Stewardship routes (Phase D.52).

A governed COMPOSITION surface over the platform's authoritative data owners — the D.23 Governance package
(catalog / quality / MDM / retention / overview), the Person-merge / entity-resolution engine, the Event
registry, and the domain entity owners. Reads only — no second master-data platform / identity system /
metadata repository / synchronization engine / merge engine, no mutation. Routes are gated by
``governance.view``; each panel additionally self-restricts to its own capability. Diagnostics is gated by
``observability.audit``. No client-sensitive data or entity payloads are ever returned — counts + status
only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.data_governance import (
    compose_dashboard,
    get_panel,
    governance_summary,
    list_dashboards,
)
from app.services.data_governance.diagnostics import governance_diagnostics
from app.services.data_governance.metrics import data_governance_metrics

router = APIRouter(tags=["data-governance"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/data-governance", response_class=HTMLResponse)
def governance_home(request: Request, dashboard: str | None = None,
                    principal: Principal = Depends(require_capability("governance.view"))):
    """The data-governance dashboard (HTML). Renders the requested dashboard, or the first available."""
    accessible = list_dashboards(principal).get("dashboards", [])
    keys = [d["key"] for d in accessible]
    chosen = dashboard if dashboard in keys else (keys[0] if keys else None)
    result = compose_dashboard(principal, chosen) if chosen else None
    return templates.TemplateResponse(request=request, name="data_governance/home.html",
                                      context={"result": result, "dashboards": accessible, "chosen": chosen,
                                               "summary": governance_summary(principal)})


@router.get("/api/v1/data-governance/dashboards")
def api_governance_dashboards(principal: Principal = Depends(require_capability("governance.view"))):
    """The governance dashboards the principal may open (JSON, metadata only)."""
    return JSONResponse(list_dashboards(principal))


@router.get("/api/v1/data-governance/dashboard/{key}")
def api_governance_dashboard(key: str, principal: Principal = Depends(require_capability("governance.view"))):
    """Compose a named governance dashboard (JSON). 404 when not registered or the principal lacks its
    required capability."""
    result = compose_dashboard(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/data-governance/summary")
def api_governance_summary(principal: Principal = Depends(require_capability("governance.view"))):
    """The firm data-governance summary (JSON) — compact, non-leaking. Backs the workspace panel + the
    Client 360 / Household 360 sections + AI grounding."""
    return JSONResponse(governance_summary(principal))


@router.get("/api/v1/data-governance/registry")
def api_governance_registry(principal: Principal = Depends(require_capability("governance.view"))):
    """The master-data + stewardship + panel + dashboard registries (JSON) — the declarative catalogs."""
    from app.services.data_governance import registry
    return JSONResponse({
        "governed_entities": [{"key": e.key, "label": e.label, "authoritative_owner": e.authoritative_owner,
                               "identity_owner": e.identity_owner, "metadata_owner": e.metadata_owner,
                               "stewardship_owner": e.stewardship_owner, "lineage_owner": e.lineage_owner,
                               "runtime_gate": e.runtime_gate, "deep_links": list(e.deep_links)}
                              for e in registry.MASTER_DATA_REGISTRY],
        "stewardship_roles": [{"key": s.key, "label": s.label, "business_owner": s.business_owner,
                               "technical_owner": s.technical_owner, "validation_owner": s.validation_owner,
                               "approval_owner": s.approval_owner, "runtime_gate": s.runtime_gate}
                              for s in registry.STEWARDSHIP_REGISTRY],
        "panels": [{"key": p.key, "owner": p.owner, "source": p.source, "measure": p.measure,
                    "permission": p.permission, "deep_link": p.deep_link, "explainability": p.explainability}
                   for p in registry.PANEL_REGISTRY],
        "dashboards": [{"key": d.key, "audience": d.audience, "runtime_gate": d.runtime_gate,
                        "panels": list(d.panels), "required_capabilities": list(d.required_capabilities),
                        "navigation": d.navigation, "governing_services": list(d.governing_services)}
                       for d in registry.GOVERNANCE_DASHBOARDS],
        "coverage": registry.coverage()})


@router.get("/api/v1/data-governance/panel/{key}")
def api_governance_panel(key: str, principal: Principal = Depends(require_capability("governance.view"))):
    """Compose a single governance panel (JSON). 404 when not registered."""
    result = get_panel(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/data-governance/metrics")
def api_governance_metrics(principal: Principal = Depends(require_capability("governance.view"))):
    """Low-cardinality data-governance-layer metrics (JSON)."""
    return JSONResponse(data_governance_metrics(principal))


@router.get("/data-governance/diagnostics")
def governance_diag(principal: Principal = Depends(require_capability("observability.audit"))):
    """Internal-only data-governance diagnostics (registry coverage, panel availability, governance)."""
    return JSONResponse(governance_diagnostics())
