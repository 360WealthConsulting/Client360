"""Enterprise Knowledge Management, SOP Governance & Institutional Intelligence routes (Phase D.62).

A governed COMPOSITION surface over the platform's authoritative knowledge / SOP / documentation owners — the
Document Platform, Document Intelligence, and Data Governance retention. Reads only — no second wiki /
document-management platform / Confluence replacement / SharePoint / records-management platform / search
engine / AI knowledge store / document repository, no mutation. Routes are gated by ``documents.view`` OR
``analytics.executive``; each panel additionally self-restricts to its authoritative-source capability.
Diagnostics is gated by ``observability.audit``. No document contents, confidential procedures, credentials,
tokens, or client-sensitive documentation are ever returned — counts, status, and coverage only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_any_capability
from app.security.models import Principal
from app.services.knowledge_management import (
    compose_dashboard,
    get_panel,
    knowledge_summary,
    list_dashboards,
)
from app.services.knowledge_management.diagnostics import knowledge_diagnostics
from app.services.knowledge_management.metrics import knowledge_management_metrics

router = APIRouter(tags=["knowledge-management"])
templates = Jinja2Templates(directory="app/templates")

# Knowledge management is a documentation / executive surface — either capability may open it.
_KM_GATE = require_any_capability("documents.view", "analytics.executive")


@router.get("/knowledge-management", response_class=HTMLResponse)
def knowledge_home(request: Request, dashboard: str | None = None,
                   principal: Principal = Depends(_KM_GATE)):
    """The knowledge-management dashboard (HTML). Renders the requested dashboard, or the first available."""
    accessible = list_dashboards(principal).get("dashboards", [])
    keys = [d["key"] for d in accessible]
    chosen = dashboard if dashboard in keys else (keys[0] if keys else None)
    result = compose_dashboard(principal, chosen) if chosen else None
    return templates.TemplateResponse(request=request, name="knowledge_management/home.html",
                                      context={"result": result, "dashboards": accessible, "chosen": chosen,
                                               "summary": knowledge_summary(principal)})


@router.get("/api/v1/knowledge-management/dashboards")
def api_knowledge_dashboards(principal: Principal = Depends(_KM_GATE)):
    """The knowledge dashboards the principal may open (JSON, metadata only)."""
    return JSONResponse(list_dashboards(principal))


@router.get("/api/v1/knowledge-management/dashboard/{key}")
def api_knowledge_dashboard(key: str, principal: Principal = Depends(_KM_GATE)):
    """Compose a named knowledge dashboard (JSON). 404 when not registered or the principal lacks its required
    capability."""
    result = compose_dashboard(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/knowledge-management/summary")
def api_knowledge_summary(principal: Principal = Depends(_KM_GATE)):
    """The firm knowledge & documentation summary (JSON) — compact, non-leaking. Backs the workspace panel +
    the Client 360 / Household 360 sections + AI grounding."""
    return JSONResponse(knowledge_summary(principal))


@router.get("/api/v1/knowledge-management/registry")
def api_knowledge_registry(principal: Principal = Depends(_KM_GATE)):
    """The knowledge-domain + SOP + documentation-owner + knowledge-source + publication-status + panel +
    dashboard registries (JSON) — the declarative catalogs."""
    from app.services.knowledge_management import registry

    def _entries(reg):
        return [{"key": e.key, "label": e.label, "owner": e.owner, "runtime_gate": e.runtime_gate,
                 "capabilities": list(e.capabilities), "deep_links": list(e.deep_links),
                 "config_status": e.config_status} for e in reg]
    return JSONResponse({
        "knowledge_domains": _entries(registry.KNOWLEDGE_DOMAIN_REGISTRY),
        "sop_categories": _entries(registry.SOP_CATEGORY_REGISTRY),
        "documentation_owners": _entries(registry.DOCUMENTATION_OWNER_REGISTRY),
        "knowledge_sources": _entries(registry.KNOWLEDGE_SOURCE_REGISTRY),
        "publication_statuses": _entries(registry.PUBLICATION_STATUS_REGISTRY),
        "panels": [{"key": p.key, "owner": p.owner, "source": p.source, "measure": p.measure,
                    "permission": p.permission, "deep_link": p.deep_link, "derived": p.derived,
                    "explainability": p.explainability} for p in registry.PANEL_REGISTRY],
        "dashboards": [{"key": d.key, "audience": d.audience, "runtime_gate": d.runtime_gate,
                        "panels": list(d.panels), "required_capabilities": list(d.required_capabilities),
                        "navigation": d.navigation, "governing_services": list(d.governing_services)}
                       for d in registry.KNOWLEDGE_DASHBOARDS],
        "coverage": registry.coverage()})


@router.get("/api/v1/knowledge-management/panel/{key}")
def api_knowledge_panel(key: str, principal: Principal = Depends(_KM_GATE)):
    """Compose a single knowledge panel (JSON). 404 when not registered."""
    result = get_panel(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/knowledge-management/metrics")
def api_knowledge_metrics(principal: Principal = Depends(_KM_GATE)):
    """Low-cardinality knowledge-management-layer metrics (JSON)."""
    return JSONResponse(knowledge_management_metrics(principal))


@router.get("/knowledge-management/diagnostics")
def knowledge_diag(principal: Principal = Depends(require_any_capability("observability.audit"))):
    """Internal-only knowledge-management diagnostics (registry coverage, panel availability, governance)."""
    return JSONResponse(knowledge_diagnostics())
