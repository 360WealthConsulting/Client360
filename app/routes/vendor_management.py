"""Enterprise Vendor Management, Third-Party Risk & Technology Lifecycle Governance routes (Phase D.56).

A governed COMPOSITION surface over the platform's authoritative vendor / technology owners — the Integration
Platform provider registry, the Security certificate & secret store, the Observability service catalog,
Insurance licensing, and Security incidents + Compliance Intelligence. Reads only — no second
vendor-management platform / procurement system / contract repository / CMDB / licensing platform / risk
engine, no mutation. Routes are gated by ``integration.view``; each panel additionally self-restricts to its
own capability (risk panels require ``security.view``). Diagnostics is gated by ``observability.audit``. No
contract contents, credentials, license keys, secrets, or procurement payloads are ever returned — counts +
status only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.vendor_management import (
    compose_dashboard,
    get_panel,
    list_dashboards,
    vendor_summary,
)
from app.services.vendor_management.diagnostics import vendor_diagnostics
from app.services.vendor_management.metrics import vendor_management_metrics

router = APIRouter(tags=["vendor-management"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/vendor-management", response_class=HTMLResponse)
def vendor_home(request: Request, dashboard: str | None = None,
                principal: Principal = Depends(require_capability("integration.view"))):
    """The vendor-management dashboard (HTML). Renders the requested dashboard, or the first available."""
    accessible = list_dashboards(principal).get("dashboards", [])
    keys = [d["key"] for d in accessible]
    chosen = dashboard if dashboard in keys else (keys[0] if keys else None)
    result = compose_dashboard(principal, chosen) if chosen else None
    return templates.TemplateResponse(request=request, name="vendor_management/home.html",
                                      context={"result": result, "dashboards": accessible, "chosen": chosen,
                                               "summary": vendor_summary(principal)})


@router.get("/api/v1/vendor-management/dashboards")
def api_vendor_dashboards(principal: Principal = Depends(require_capability("integration.view"))):
    """The vendor dashboards the principal may open (JSON, metadata only)."""
    return JSONResponse(list_dashboards(principal))


@router.get("/api/v1/vendor-management/dashboard/{key}")
def api_vendor_dashboard(key: str, principal: Principal = Depends(require_capability("integration.view"))):
    """Compose a named vendor dashboard (JSON). 404 when not registered or the principal lacks its required
    capability."""
    result = compose_dashboard(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/vendor-management/summary")
def api_vendor_summary(principal: Principal = Depends(require_capability("integration.view"))):
    """The firm vendor / technology-health summary (JSON) — compact, non-leaking. Backs the workspace panel +
    the Client 360 / Household 360 sections + AI grounding."""
    return JSONResponse(vendor_summary(principal))


@router.get("/api/v1/vendor-management/registry")
def api_vendor_registry(principal: Principal = Depends(require_capability("integration.view"))):
    """The vendor + technology-lifecycle + panel + dashboard registries (JSON) — the declarative catalogs."""
    from app.services.vendor_management import registry
    return JSONResponse({
        "vendor_classes": [{"key": v.key, "label": v.label, "authoritative_owner": v.authoritative_owner,
                            "integration_owner": v.integration_owner, "security_owner": v.security_owner,
                            "lifecycle_owner": v.lifecycle_owner, "provider_type": v.provider_type,
                            "runtime_gate": v.runtime_gate, "deep_links": list(v.deep_links)}
                           for v in registry.VENDOR_REGISTRY],
        "lifecycle_classes": [{"key": t.key, "label": t.label, "category": t.category, "owner": t.owner,
                               "lifecycle_owner": t.lifecycle_owner, "renewal_owner": t.renewal_owner,
                               "support_owner": t.support_owner, "runtime_gate": t.runtime_gate}
                              for t in registry.TECHNOLOGY_LIFECYCLE_REGISTRY],
        "panels": [{"key": p.key, "owner": p.owner, "source": p.source, "measure": p.measure,
                    "permission": p.permission, "deep_link": p.deep_link, "explainability": p.explainability}
                   for p in registry.PANEL_REGISTRY],
        "dashboards": [{"key": d.key, "audience": d.audience, "runtime_gate": d.runtime_gate,
                        "panels": list(d.panels), "required_capabilities": list(d.required_capabilities),
                        "navigation": d.navigation, "governing_services": list(d.governing_services)}
                       for d in registry.VENDOR_DASHBOARDS],
        "coverage": registry.coverage()})


@router.get("/api/v1/vendor-management/panel/{key}")
def api_vendor_panel(key: str, principal: Principal = Depends(require_capability("integration.view"))):
    """Compose a single vendor panel (JSON). 404 when not registered."""
    result = get_panel(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/vendor-management/metrics")
def api_vendor_metrics(principal: Principal = Depends(require_capability("integration.view"))):
    """Low-cardinality vendor-management-layer metrics (JSON)."""
    return JSONResponse(vendor_management_metrics(principal))


@router.get("/vendor-management/diagnostics")
def vendor_diag(principal: Principal = Depends(require_capability("observability.audit"))):
    """Internal-only vendor-management diagnostics (registry coverage, panel availability, governance)."""
    return JSONResponse(vendor_diagnostics())
