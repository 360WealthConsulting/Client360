"""Enterprise Environment Management, Deployment Topology & Platform Lifecycle Intelligence routes (Phase D.64).

A governed COMPOSITION surface over the platform's authoritative environment / platform / deployment-topology /
lifecycle / infrastructure-dependency owners — the Observability catalog (environment profiles, deployment
references, service inventory, the service dependency graph), the Observability health owner (runtime
snapshots, the live migration head), the Observability service overview, the Runtime + Policy engines, and the
Integration platform. Reads only — no second CMDB / infrastructure-management platform / cloud-management
platform / deployment orchestrator / asset inventory / configuration database / environment manager /
monitoring platform, no mutation (never create an environment / deploy / provision / modify topology / change
lifecycle / execute a cloud operation / write configuration / delete an environment). Routes are gated by
``observability.view`` OR ``analytics.executive``; each panel additionally self-restricts to its
authoritative-source capability. Diagnostics is gated by ``observability.audit``. No credentials, secrets,
tokens, environment variables, connection strings, private keys, deployment payloads, protected infrastructure
details, private topology, or sensitive configuration values are ever returned — counts, status, identifiers,
coverage, and verification only. Environment metadata is not live infrastructure; a deployment reference is not
a deployment.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_any_capability
from app.security.models import Principal
from app.services.environment_management import (
    compose_dashboard,
    environment_summary,
    get_panel,
    list_dashboards,
)
from app.services.environment_management.diagnostics import environment_diagnostics
from app.services.environment_management.metrics import environment_management_metrics

router = APIRouter(tags=["environment-management"])
templates = Jinja2Templates(directory="app/templates")

# Environment management is an operational / executive surface — either capability may open it.
_EM_GATE = require_any_capability("observability.view", "analytics.executive")


@router.get("/environment-management", response_class=HTMLResponse)
def environment_home(request: Request, dashboard: str | None = None,
                     principal: Principal = Depends(_EM_GATE)):
    """The environment-management dashboard (HTML). Renders the requested dashboard, or the first available."""
    accessible = list_dashboards(principal).get("dashboards", [])
    keys = [d["key"] for d in accessible]
    chosen = dashboard if dashboard in keys else (keys[0] if keys else None)
    result = compose_dashboard(principal, chosen) if chosen else None
    return templates.TemplateResponse(request=request, name="environment_management/home.html",
                                      context={"result": result, "dashboards": accessible, "chosen": chosen,
                                               "summary": environment_summary(principal)})


@router.get("/api/v1/environment-management/dashboards")
def api_environment_dashboards(principal: Principal = Depends(_EM_GATE)):
    """The environment dashboards the principal may open (JSON, metadata only)."""
    return JSONResponse(list_dashboards(principal))


@router.get("/api/v1/environment-management/dashboard/{key}")
def api_environment_dashboard(key: str, principal: Principal = Depends(_EM_GATE)):
    """Compose a named environment dashboard (JSON). 404 when not registered or the principal lacks its
    required capability."""
    result = compose_dashboard(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/environment-management/summary")
def api_environment_summary(principal: Principal = Depends(_EM_GATE)):
    """The firm environment / platform / deployment / lifecycle summary (JSON) — compact, non-leaking. Backs
    the workspace panel + the Client 360 / Household 360 sections + AI grounding. Operational visibility only
    — environment metadata is not live infrastructure, a deployment reference is not a deployment."""
    return JSONResponse(environment_summary(principal))


@router.get("/api/v1/environment-management/registry")
def api_environment_registry(principal: Principal = Depends(_EM_GATE)):
    """The environment + platform + deployment-topology + lifecycle + infrastructure-dependency + panel +
    dashboard registries (JSON) — the declarative catalogs. Metadata only — never a sensitive configuration
    value or private topology."""
    from app.services.environment_management import registry

    def _entries(reg):
        return [{"key": e.key, "label": e.label, "owner": e.owner, "runtime_gate": e.runtime_gate,
                 "capabilities": list(e.capabilities), "environment_scope": e.environment_scope,
                 "deep_links": list(e.deep_links), "config_status": e.config_status} for e in reg]
    return JSONResponse({
        "environment_domains": _entries(registry.ENVIRONMENT_REGISTRY),
        "platform_domains": _entries(registry.PLATFORM_REGISTRY),
        "deployment_topology_domains": _entries(registry.DEPLOYMENT_TOPOLOGY_REGISTRY),
        "lifecycle_domains": _entries(registry.LIFECYCLE_REGISTRY),
        "infrastructure_dependency_domains": _entries(registry.INFRASTRUCTURE_DEPENDENCY_REGISTRY),
        "panels": [{"key": p.key, "owner": p.owner, "source": p.source, "measure": p.measure,
                    "permission": p.permission, "deep_link": p.deep_link, "derived": p.derived,
                    "explainability": p.explainability} for p in registry.PANEL_REGISTRY],
        "dashboards": [{"key": d.key, "audience": d.audience, "runtime_gate": d.runtime_gate,
                        "panels": list(d.panels), "required_capabilities": list(d.required_capabilities),
                        "navigation": d.navigation, "governing_services": list(d.governing_services)}
                       for d in registry.ENVIRONMENT_DASHBOARDS],
        "coverage": registry.coverage()})


@router.get("/api/v1/environment-management/panel/{key}")
def api_environment_panel(key: str, principal: Principal = Depends(_EM_GATE)):
    """Compose a single environment panel (JSON). 404 when not registered."""
    result = get_panel(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/environment-management/metrics")
def api_environment_metrics(principal: Principal = Depends(_EM_GATE)):
    """Low-cardinality environment-management-layer metrics (JSON)."""
    return JSONResponse(environment_management_metrics(principal))


@router.get("/environment-management/diagnostics")
def environment_diag(principal: Principal = Depends(require_any_capability("observability.audit"))):
    """Internal-only environment-management diagnostics (registry coverage, panel availability, governance)."""
    return JSONResponse(environment_diagnostics())
