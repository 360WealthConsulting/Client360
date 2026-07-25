"""Enterprise Change Management, Release Governance & Configuration Intelligence routes (Phase D.63).

A governed COMPOSITION surface over the platform's authoritative change / release / configuration / evidence
owners — the architecture manifest, the live Alembic script head, the live route / ADR / section / dashboard
counts, the Runtime + Policy engines, the Observability catalog / alerts / incidents / health owners, Security
incidents, Compliance Intelligence, and the CI pipeline evidence the manifest records. Reads only — no second
ITSM / change-management / deployment / CI-CD / Git / CMDB / feature-flag / release-approval / incident /
maintenance-scheduling platform, no mutation (never a branch / merge / commit / tag / deploy / migration / flag
/ approval / rollback / maintenance write). Routes are gated by ``observability.view`` OR ``analytics.executive``;
each panel additionally self-restricts to its authoritative-source capability. Diagnostics is gated by
``observability.audit``. No credentials, secrets, tokens, environment variables, connection strings, private
keys, deployment payloads, protected infrastructure details, sensitive configuration values, private incident
narratives, or repository credentials are ever returned — counts, status, identifiers, hashes, timestamps,
coverage, and verification only. Green CI is not production certification, merged is not deployed.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_any_capability
from app.security.models import Principal
from app.services.change_management import (
    change_summary,
    compose_dashboard,
    get_panel,
    list_dashboards,
)
from app.services.change_management.diagnostics import change_diagnostics
from app.services.change_management.metrics import change_management_metrics

router = APIRouter(tags=["change-management"])
templates = Jinja2Templates(directory="app/templates")

# Change management is an operational / executive surface — either capability may open it.
_CM_GATE = require_any_capability("observability.view", "analytics.executive")


@router.get("/change-management", response_class=HTMLResponse)
def change_home(request: Request, dashboard: str | None = None,
                principal: Principal = Depends(_CM_GATE)):
    """The change-management dashboard (HTML). Renders the requested dashboard, or the first available."""
    accessible = list_dashboards(principal).get("dashboards", [])
    keys = [d["key"] for d in accessible]
    chosen = dashboard if dashboard in keys else (keys[0] if keys else None)
    result = compose_dashboard(principal, chosen) if chosen else None
    return templates.TemplateResponse(request=request, name="change_management/home.html",
                                      context={"result": result, "dashboards": accessible, "chosen": chosen,
                                               "summary": change_summary(principal)})


@router.get("/api/v1/change-management/dashboards")
def api_change_dashboards(principal: Principal = Depends(_CM_GATE)):
    """The change dashboards the principal may open (JSON, metadata only)."""
    return JSONResponse(list_dashboards(principal))


@router.get("/api/v1/change-management/dashboard/{key}")
def api_change_dashboard(key: str, principal: Principal = Depends(_CM_GATE)):
    """Compose a named change dashboard (JSON). 404 when not registered or the principal lacks its required
    capability."""
    result = compose_dashboard(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/change-management/summary")
def api_change_summary(principal: Principal = Depends(_CM_GATE)):
    """The firm change / release / configuration summary (JSON) — compact, non-leaking. Backs the workspace
    panel + the Client 360 / Household 360 sections + AI grounding. Operational readiness only — green CI is
    not production, merged is not deployed."""
    return JSONResponse(change_summary(principal))


@router.get("/api/v1/change-management/registry")
def api_change_registry(principal: Principal = Depends(_CM_GATE)):
    """The change-domain + release + configuration + change-evidence + panel + dashboard registries (JSON) —
    the declarative catalogs. Metadata only — never a sensitive configuration value."""
    from app.services.change_management import registry

    def _entries(reg):
        out = []
        for e in reg:
            links = list(getattr(e, "deep_links", ()) or ())
            if not links and getattr(e, "deep_link", None):
                links = [e.deep_link]
            out.append({"key": e.key, "label": e.label, "owner": e.owner, "runtime_gate": e.runtime_gate,
                        "capabilities": list(e.capabilities), "deep_links": links,
                        "config_status": e.config_status})
        return out
    return JSONResponse({
        "change_domains": _entries(registry.CHANGE_DOMAIN_REGISTRY),
        "release_entries": _entries(registry.RELEASE_REGISTRY),
        "configuration_entries": _entries(registry.CONFIGURATION_REGISTRY),
        "change_evidence": _entries(registry.CHANGE_EVIDENCE_REGISTRY),
        "panels": [{"key": p.key, "owner": p.owner, "source": p.source, "measure": p.measure,
                    "permission": p.permission, "deep_link": p.deep_link, "derived": p.derived,
                    "explainability": p.explainability} for p in registry.PANEL_REGISTRY],
        "dashboards": [{"key": d.key, "audience": d.audience, "runtime_gate": d.runtime_gate,
                        "panels": list(d.panels), "required_capabilities": list(d.required_capabilities),
                        "navigation": d.navigation, "governing_services": list(d.governing_services)}
                       for d in registry.CHANGE_DASHBOARDS],
        "coverage": registry.coverage()})


@router.get("/api/v1/change-management/panel/{key}")
def api_change_panel(key: str, principal: Principal = Depends(_CM_GATE)):
    """Compose a single change panel (JSON). 404 when not registered."""
    result = get_panel(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/change-management/metrics")
def api_change_metrics(principal: Principal = Depends(_CM_GATE)):
    """Low-cardinality change-management-layer metrics (JSON)."""
    return JSONResponse(change_management_metrics(principal))


@router.get("/change-management/diagnostics")
def change_diag(principal: Principal = Depends(require_any_capability("observability.audit"))):
    """Internal-only change-management diagnostics (registry coverage, panel availability, governance)."""
    return JSONResponse(change_diagnostics())
