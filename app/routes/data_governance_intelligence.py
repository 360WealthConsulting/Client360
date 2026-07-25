"""Enterprise Data Governance, Lineage & Information Stewardship Intelligence routes (Phase D.66).

A governed COMPOSITION surface over the platform's authoritative data-governance owners — the Governance
catalog (data domains, elements, quality rules, survivorship rules, stewardship), Governance MDM (lineage &
provenance, merge candidates), Governance Quality (findings), and Governance Retention (assignments, legal
holds, deletion requests, cases). Reads only — no second data catalog / metadata repository / ETL platform /
MDM platform / warehouse / governance platform / lineage engine / quality engine, no mutation (never transform
/ synchronize / mutate metadata / repair / create lineage / assign a steward / execute a quality rule / enforce
retention). Routes are gated by ``governance.view`` OR ``analytics.executive``; each panel additionally
self-restricts to its authoritative-source capability. Diagnostics is gated by ``observability.audit``. No
sensitive data values, client PII, credentials, secrets, tokens, confidential metadata, internal governance
notes, or quality-rule internals are ever returned — counts, coverage, status, and ratios only. A registered
rule is not an executed check; coverage is not certification. (Distinct from the D.52 Data Governance layer at
`/data-governance`; both are read-only views over the single authoritative D.23 Governance package.)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_any_capability
from app.security.models import Principal
from app.services.data_governance_intelligence import (
    compose_dashboard,
    data_governance_summary,
    get_panel,
    list_dashboards,
)
from app.services.data_governance_intelligence.diagnostics import data_governance_diagnostics
from app.services.data_governance_intelligence.metrics import data_governance_intelligence_metrics

router = APIRouter(tags=["data-governance-intelligence"])
templates = Jinja2Templates(directory="app/templates")

# Data-governance intelligence is a governance / executive surface — either capability may open it.
_DG_GATE = require_any_capability("governance.view", "analytics.executive")


@router.get("/data-governance-intelligence", response_class=HTMLResponse)
def data_governance_home(request: Request, dashboard: str | None = None,
                         principal: Principal = Depends(_DG_GATE)):
    """The data-governance-intelligence dashboard (HTML). Renders the requested dashboard, or the first
    available."""
    accessible = list_dashboards(principal).get("dashboards", [])
    keys = [d["key"] for d in accessible]
    chosen = dashboard if dashboard in keys else (keys[0] if keys else None)
    result = compose_dashboard(principal, chosen) if chosen else None
    return templates.TemplateResponse(request=request, name="data_governance_intelligence/home.html",
                                      context={"result": result, "dashboards": accessible, "chosen": chosen,
                                               "summary": data_governance_summary(principal)})


@router.get("/api/v1/data-governance-intelligence/dashboards")
def api_dg_dashboards(principal: Principal = Depends(_DG_GATE)):
    """The data-governance dashboards the principal may open (JSON, metadata only)."""
    return JSONResponse(list_dashboards(principal))


@router.get("/api/v1/data-governance-intelligence/dashboard/{key}")
def api_dg_dashboard(key: str, principal: Principal = Depends(_DG_GATE)):
    """Compose a named data-governance dashboard (JSON). 404 when not registered or the principal lacks its
    required capability."""
    result = compose_dashboard(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/data-governance-intelligence/summary")
def api_dg_summary(principal: Principal = Depends(_DG_GATE)):
    """The firm data-governance summary (JSON) — compact, non-leaking. Backs the workspace panel + the Client
    360 / Household 360 sections + AI grounding. Governance coverage only — a registered rule is not an executed
    check, coverage is not certification."""
    return JSONResponse(data_governance_summary(principal))


@router.get("/api/v1/data-governance-intelligence/registry")
def api_dg_registry(principal: Principal = Depends(_DG_GATE)):
    """The data-domain + lineage + stewardship + quality + retention + panel + dashboard registries (JSON) —
    the declarative catalogs. Metadata only — never confidential metadata or a quality-rule internal."""
    from app.services.data_governance_intelligence import registry

    def _entries(reg):
        return [{"key": e.key, "label": e.label, "owner": e.owner, "runtime_gate": e.runtime_gate,
                 "capabilities": list(e.capabilities), "deep_links": list(e.deep_links),
                 "config_status": e.config_status} for e in reg]
    return JSONResponse({
        "data_domain_entries": _entries(registry.DATA_DOMAIN_REGISTRY),
        "lineage_entries": _entries(registry.DATA_LINEAGE_REGISTRY),
        "stewardship_entries": _entries(registry.DATA_STEWARDSHIP_REGISTRY),
        "quality_entries": _entries(registry.DATA_QUALITY_REGISTRY),
        "retention_entries": _entries(registry.DATA_RETENTION_REGISTRY),
        "panels": [{"key": p.key, "owner": p.owner, "source": p.source, "measure": p.measure,
                    "permission": p.permission, "deep_link": p.deep_link, "derived": p.derived,
                    "explainability": p.explainability} for p in registry.PANEL_REGISTRY],
        "dashboards": [{"key": d.key, "audience": d.audience, "runtime_gate": d.runtime_gate,
                        "panels": list(d.panels), "required_capabilities": list(d.required_capabilities),
                        "navigation": d.navigation, "governing_services": list(d.governing_services)}
                       for d in registry.DATA_GOVERNANCE_DASHBOARDS],
        "coverage": registry.coverage()})


@router.get("/api/v1/data-governance-intelligence/panel/{key}")
def api_dg_panel(key: str, principal: Principal = Depends(_DG_GATE)):
    """Compose a single data-governance panel (JSON). 404 when not registered."""
    result = get_panel(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/data-governance-intelligence/metrics")
def api_dg_metrics(principal: Principal = Depends(_DG_GATE)):
    """Low-cardinality data-governance-intelligence-layer metrics (JSON)."""
    return JSONResponse(data_governance_intelligence_metrics(principal))


@router.get("/data-governance-intelligence/diagnostics")
def dg_diag(principal: Principal = Depends(require_any_capability("observability.audit"))):
    """Internal-only data-governance-intelligence diagnostics (registry coverage, panel availability,
    governance)."""
    return JSONResponse(data_governance_diagnostics())
