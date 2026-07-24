"""Enterprise Integration Hub & Connected Platform Governance routes (Phase D.53).

A governed COMPOSITION surface over the platform's authoritative integration owners — the D.24 Integration
Platform (service / sync / connectors / webhooks / api / events), the Event outbox + Event registry, and the
M365 / insurance / signature connectors. Reads only — no second integration platform / ESB / API gateway /
synchronization engine / webhook processor / event bus, no mutation, no synchronization trigger, no API
invocation. Routes are gated by ``integration.view``; each panel additionally self-restricts to its own
capability. Diagnostics is gated by ``observability.audit``. No secrets, tokens, credentials, or client
payloads are ever returned — counts + status only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.integration_hub import (
    compose_dashboard,
    get_panel,
    integration_summary,
    list_dashboards,
)
from app.services.integration_hub.diagnostics import integration_diagnostics
from app.services.integration_hub.metrics import integration_hub_metrics

router = APIRouter(tags=["integration-hub"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/integration-hub", response_class=HTMLResponse)
def integration_home(request: Request, dashboard: str | None = None,
                     principal: Principal = Depends(require_capability("integration.view"))):
    """The integration-hub dashboard (HTML). Renders the requested dashboard, or the first available."""
    accessible = list_dashboards(principal).get("dashboards", [])
    keys = [d["key"] for d in accessible]
    chosen = dashboard if dashboard in keys else (keys[0] if keys else None)
    result = compose_dashboard(principal, chosen) if chosen else None
    return templates.TemplateResponse(request=request, name="integration_hub/home.html",
                                      context={"result": result, "dashboards": accessible, "chosen": chosen,
                                               "summary": integration_summary(principal)})


@router.get("/api/v1/integration-hub/dashboards")
def api_integration_dashboards(principal: Principal = Depends(require_capability("integration.view"))):
    """The integration dashboards the principal may open (JSON, metadata only)."""
    return JSONResponse(list_dashboards(principal))


@router.get("/api/v1/integration-hub/dashboard/{key}")
def api_integration_dashboard(key: str, principal: Principal = Depends(require_capability("integration.view"))):
    """Compose a named integration dashboard (JSON). 404 when not registered or the principal lacks its
    required capability."""
    result = compose_dashboard(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/integration-hub/summary")
def api_integration_summary(principal: Principal = Depends(require_capability("integration.view"))):
    """The firm integration-health summary (JSON) — compact, non-leaking. Backs the workspace panel + the
    Client 360 / Household 360 sections + AI grounding."""
    return JSONResponse(integration_summary(principal))


@router.get("/api/v1/integration-hub/registry")
def api_integration_registry(principal: Principal = Depends(require_capability("integration.view"))):
    """The integration + connector + panel + dashboard registries (JSON) — the declarative catalogs."""
    from app.services.integration_hub import registry
    return JSONResponse({
        "integrations": [{"key": i.key, "label": i.label, "authoritative_owner": i.authoritative_owner,
                          "connection_owner": i.connection_owner,
                          "authentication_owner": i.authentication_owner,
                          "synchronization_owner": i.synchronization_owner, "provider_type": i.provider_type,
                          "runtime_gate": i.runtime_gate, "deep_links": list(i.deep_links)}
                         for i in registry.INTEGRATION_REGISTRY],
        "connectors": [{"key": c.key, "label": c.label, "protocol": c.protocol,
                        "authentication": c.authentication, "polling_owner": c.polling_owner,
                        "webhook_owner": c.webhook_owner, "retry_owner": c.retry_owner,
                        "monitoring_owner": c.monitoring_owner, "runtime_gate": c.runtime_gate}
                       for c in registry.CONNECTOR_REGISTRY],
        "panels": [{"key": p.key, "owner": p.owner, "source": p.source, "measure": p.measure,
                    "permission": p.permission, "deep_link": p.deep_link, "explainability": p.explainability}
                   for p in registry.PANEL_REGISTRY],
        "dashboards": [{"key": d.key, "audience": d.audience, "runtime_gate": d.runtime_gate,
                        "panels": list(d.panels), "required_capabilities": list(d.required_capabilities),
                        "navigation": d.navigation, "governing_services": list(d.governing_services)}
                       for d in registry.INTEGRATION_DASHBOARDS],
        "coverage": registry.coverage()})


@router.get("/api/v1/integration-hub/panel/{key}")
def api_integration_panel(key: str, principal: Principal = Depends(require_capability("integration.view"))):
    """Compose a single integration panel (JSON). 404 when not registered."""
    result = get_panel(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/integration-hub/metrics")
def api_integration_metrics(principal: Principal = Depends(require_capability("integration.view"))):
    """Low-cardinality integration-hub-layer metrics (JSON)."""
    return JSONResponse(integration_hub_metrics(principal))


@router.get("/integration-hub/diagnostics")
def integration_diag(principal: Principal = Depends(require_capability("observability.audit"))):
    """Internal-only integration-hub diagnostics (registry coverage, panel availability, governance)."""
    return JSONResponse(integration_diagnostics())
