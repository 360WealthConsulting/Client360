"""Enterprise Document Intelligence & Records Lifecycle routes (Phase D.50).

A governed COMPOSITION surface over the platform's authoritative document systems — the Document Platform
(the single document + metadata + lifecycle + retention-policy owner), Governance retention, and Compliance
Intelligence. Reads only — no second DMS/OCR/index/archive/metadata/records store, no mutation. Routes are
gated by ``documents.view``; each panel additionally self-restricts to its own capability. Diagnostics is
gated by ``observability.audit``. No document content is ever returned — counts + status only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.document_intelligence import (
    compose_dashboard,
    document_summary,
    get_panel,
    list_dashboards,
)
from app.services.document_intelligence.diagnostics import document_diagnostics
from app.services.document_intelligence.metrics import document_intelligence_metrics

router = APIRouter(tags=["document-intelligence"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/document-intelligence", response_class=HTMLResponse)
def document_home(request: Request, dashboard: str | None = None,
                  principal: Principal = Depends(require_capability("documents.view"))):
    """The document-intelligence dashboard (HTML). Renders the requested dashboard, or the first available."""
    accessible = list_dashboards(principal).get("dashboards", [])
    keys = [d["key"] for d in accessible]
    chosen = dashboard if dashboard in keys else (keys[0] if keys else None)
    result = compose_dashboard(principal, chosen) if chosen else None
    return templates.TemplateResponse(request=request, name="document_intelligence/home.html",
                                      context={"result": result, "dashboards": accessible, "chosen": chosen,
                                               "summary": document_summary(principal)})


@router.get("/api/v1/document-intelligence/dashboards")
def api_document_dashboards(principal: Principal = Depends(require_capability("documents.view"))):
    """The document dashboards the principal may open (JSON, metadata only)."""
    return JSONResponse(list_dashboards(principal))


@router.get("/api/v1/document-intelligence/dashboard/{key}")
def api_document_dashboard(key: str, principal: Principal = Depends(require_capability("documents.view"))):
    """Compose a named document dashboard (JSON). 404 when not registered or the principal lacks its
    required capability."""
    result = compose_dashboard(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/document-intelligence/summary")
def api_document_summary(principal: Principal = Depends(require_capability("documents.view"))):
    """The firm document-intelligence summary (JSON) — compact, non-leaking. Backs the workspace panel + the
    Client 360 / Household 360 sections + AI grounding."""
    return JSONResponse(document_summary(principal))


@router.get("/api/v1/document-intelligence/registry")
def api_document_registry(principal: Principal = Depends(require_capability("documents.view"))):
    """The document + retention + panel + dashboard registries (JSON) — the declarative catalogs."""
    from app.services.document_intelligence import registry
    return JSONResponse({
        "document_classes": [{"key": d.key, "label": d.label, "owner": d.owner,
                              "storage_source": d.storage_source, "metadata_source": d.metadata_source,
                              "classification": d.classification, "retention_policy": d.retention_policy,
                              "lifecycle": d.lifecycle, "runtime_gate": d.runtime_gate,
                              "refresh_policy": d.refresh_policy, "deep_links": list(d.deep_links)}
                             for d in registry.DOCUMENT_REGISTRY],
        "retention_policies": [{"key": r.key, "label": r.label, "owner": r.owner,
                                "retention_period": r.retention_period, "archive_owner": r.archive_owner,
                                "disposition_policy": r.disposition_policy,
                                "governing_regulation": r.governing_regulation, "runtime_gate": r.runtime_gate}
                               for r in registry.RETENTION_REGISTRY],
        "panels": [{"key": p.key, "owner": p.owner, "source": p.source, "measure": p.measure,
                    "permission": p.permission, "deep_link": p.deep_link, "explainability": p.explainability}
                   for p in registry.PANEL_REGISTRY],
        "dashboards": [{"key": d.key, "audience": d.audience, "runtime_gate": d.runtime_gate,
                        "panels": list(d.panels), "required_capabilities": list(d.required_capabilities),
                        "navigation": d.navigation, "governing_services": list(d.governing_services)}
                       for d in registry.INTELLIGENCE_DASHBOARDS],
        "coverage": registry.coverage()})


@router.get("/api/v1/document-intelligence/panel/{key}")
def api_document_panel(key: str, principal: Principal = Depends(require_capability("documents.view"))):
    """Compose a single document panel (JSON). 404 when not registered."""
    result = get_panel(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/document-intelligence/metrics")
def api_document_metrics(principal: Principal = Depends(require_capability("documents.view"))):
    """Low-cardinality document-intelligence-layer metrics (JSON)."""
    return JSONResponse(document_intelligence_metrics(principal))


@router.get("/document-intelligence/diagnostics")
def document_diag(principal: Principal = Depends(require_capability("observability.audit"))):
    """Internal-only document-intelligence diagnostics (registry coverage, panel availability, governance)."""
    return JSONResponse(document_diagnostics())
