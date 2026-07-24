"""Enterprise Automation Orchestration & Business Process Composition routes (Phase D.51).

A governed COMPOSITION surface over the platform's authoritative operational services — the Workflow Engine
(`workflow_automation` + the `workflow_orchestration` facade), the Automation scheduled-job engine, the
Trigger engine + action catalog, the Event outbox, Scheduling, and Communications. Reads only — no second
workflow engine / scheduler / rules engine / orchestration engine / event bus / automation platform, no
mutation. Routes are gated by ``automation.view``; each panel additionally self-restricts to its own
capability. Diagnostics is gated by ``observability.audit``. No workflow payloads are ever returned — counts
+ status only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.automation_orchestration import (
    automation_summary,
    compose_dashboard,
    get_panel,
    list_dashboards,
)
from app.services.automation_orchestration.diagnostics import automation_diagnostics
from app.services.automation_orchestration.metrics import automation_orchestration_metrics

router = APIRouter(tags=["automation-orchestration"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/automation-orchestration", response_class=HTMLResponse)
def automation_home(request: Request, dashboard: str | None = None,
                    principal: Principal = Depends(require_capability("automation.view"))):
    """The automation-orchestration dashboard (HTML). Renders the requested dashboard, or the first
    available."""
    accessible = list_dashboards(principal).get("dashboards", [])
    keys = [d["key"] for d in accessible]
    chosen = dashboard if dashboard in keys else (keys[0] if keys else None)
    result = compose_dashboard(principal, chosen) if chosen else None
    return templates.TemplateResponse(request=request, name="automation_orchestration/home.html",
                                      context={"result": result, "dashboards": accessible, "chosen": chosen,
                                               "summary": automation_summary(principal)})


@router.get("/api/v1/automation-orchestration/dashboards")
def api_automation_dashboards(principal: Principal = Depends(require_capability("automation.view"))):
    """The automation dashboards the principal may open (JSON, metadata only)."""
    return JSONResponse(list_dashboards(principal))


@router.get("/api/v1/automation-orchestration/dashboard/{key}")
def api_automation_dashboard(key: str, principal: Principal = Depends(require_capability("automation.view"))):
    """Compose a named automation dashboard (JSON). 404 when not registered or the principal lacks its
    required capability."""
    result = compose_dashboard(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/automation-orchestration/summary")
def api_automation_summary(principal: Principal = Depends(require_capability("automation.view"))):
    """The firm automation-orchestration summary (JSON) — compact, non-leaking. Backs the workspace panel +
    the Client 360 / Household 360 sections + AI grounding."""
    return JSONResponse(automation_summary(principal))


@router.get("/api/v1/automation-orchestration/registry")
def api_automation_registry(principal: Principal = Depends(require_capability("automation.view"))):
    """The automation + trigger + action + panel + dashboard registries (JSON) — the declarative catalogs."""
    from app.services.automation_orchestration import registry
    return JSONResponse({
        "automations": [{"key": a.key, "label": a.label, "owner": a.owner,
                         "workflow_owner": a.workflow_owner, "trigger_source": a.trigger_source,
                         "execution_owner": a.execution_owner, "scheduling_owner": a.scheduling_owner,
                         "notification_owner": a.notification_owner, "runtime_gate": a.runtime_gate,
                         "deep_links": list(a.deep_links)} for a in registry.AUTOMATION_REGISTRY],
        "triggers": [{"key": t.key, "label": t.label, "owner": t.owner, "source": t.source,
                      "execution_owner": t.execution_owner, "runtime_gate": t.runtime_gate}
                     for t in registry.TRIGGER_REGISTRY],
        "actions": [{"key": a.key, "label": a.label, "authoritative_owner": a.authoritative_owner,
                     "execution_service": a.execution_service, "permissions": list(a.permissions),
                     "runtime_gate": a.runtime_gate} for a in registry.ACTION_REGISTRY],
        "panels": [{"key": p.key, "owner": p.owner, "source": p.source, "measure": p.measure,
                    "permission": p.permission, "deep_link": p.deep_link, "explainability": p.explainability}
                   for p in registry.PANEL_REGISTRY],
        "dashboards": [{"key": d.key, "audience": d.audience, "runtime_gate": d.runtime_gate,
                        "panels": list(d.panels), "required_capabilities": list(d.required_capabilities),
                        "navigation": d.navigation, "governing_services": list(d.governing_services)}
                       for d in registry.ORCHESTRATION_DASHBOARDS],
        "coverage": registry.coverage()})


@router.get("/api/v1/automation-orchestration/panel/{key}")
def api_automation_panel(key: str, principal: Principal = Depends(require_capability("automation.view"))):
    """Compose a single automation panel (JSON). 404 when not registered."""
    result = get_panel(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/automation-orchestration/metrics")
def api_automation_metrics(principal: Principal = Depends(require_capability("automation.view"))):
    """Low-cardinality automation-orchestration-layer metrics (JSON)."""
    return JSONResponse(automation_orchestration_metrics(principal))


@router.get("/automation-orchestration/diagnostics")
def automation_diag(principal: Principal = Depends(require_capability("observability.audit"))):
    """Internal-only automation-orchestration diagnostics (registry coverage, panel availability,
    governance)."""
    return JSONResponse(automation_diagnostics())
