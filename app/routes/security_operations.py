"""Enterprise Security Operations, Identity Governance & Platform Security Intelligence routes (Phase D.54).

A governed COMPOSITION surface over the platform's authoritative security owners — the Security metadata
domain, the Identity owner, the RBAC foundation, and the hash-chain audit log. Reads only — no second IAM /
identity provider / RBAC engine / authentication system / MFA provider / audit-logging platform / SIEM, no
mutation, no auth action. Routes are gated by ``security.view``; each panel additionally self-restricts to
its own capability (audit panels compose the audit log, which enforces ``audit.read`` internally).
Diagnostics is gated by ``observability.audit``. No passwords, secrets, tokens, session IDs, or
authentication payloads are ever returned — counts + status only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.security_operations import (
    compose_dashboard,
    get_panel,
    list_dashboards,
    security_summary,
)
from app.services.security_operations.diagnostics import security_diagnostics
from app.services.security_operations.metrics import security_operations_metrics

router = APIRouter(tags=["security-operations"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/security-operations", response_class=HTMLResponse)
def security_home(request: Request, dashboard: str | None = None,
                  principal: Principal = Depends(require_capability("security.view"))):
    """The security-operations dashboard (HTML). Renders the requested dashboard, or the first available."""
    accessible = list_dashboards(principal).get("dashboards", [])
    keys = [d["key"] for d in accessible]
    chosen = dashboard if dashboard in keys else (keys[0] if keys else None)
    result = compose_dashboard(principal, chosen) if chosen else None
    return templates.TemplateResponse(request=request, name="security_operations/home.html",
                                      context={"result": result, "dashboards": accessible, "chosen": chosen,
                                               "summary": security_summary(principal)})


@router.get("/api/v1/security-operations/dashboards")
def api_security_dashboards(principal: Principal = Depends(require_capability("security.view"))):
    """The security dashboards the principal may open (JSON, metadata only)."""
    return JSONResponse(list_dashboards(principal))


@router.get("/api/v1/security-operations/dashboard/{key}")
def api_security_dashboard(key: str, principal: Principal = Depends(require_capability("security.view"))):
    """Compose a named security dashboard (JSON). 404 when not registered or the principal lacks its required
    capability."""
    result = compose_dashboard(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/security-operations/summary")
def api_security_summary(principal: Principal = Depends(require_capability("security.view"))):
    """The firm security-operations summary (JSON) — compact, non-leaking. Backs the workspace panel + the
    Client 360 / Household 360 sections + AI grounding."""
    return JSONResponse(security_summary(principal))


@router.get("/api/v1/security-operations/registry")
def api_security_registry(principal: Principal = Depends(require_capability("security.view"))):
    """The identity + security + panel + dashboard registries (JSON) — the declarative catalogs."""
    from app.services.security_operations import registry
    return JSONResponse({
        "identity_classes": [{"key": i.key, "label": i.label, "authoritative_owner": i.authoritative_owner,
                              "authentication_owner": i.authentication_owner,
                              "authorization_owner": i.authorization_owner, "runtime_gate": i.runtime_gate,
                              "deep_links": list(i.deep_links)} for i in registry.IDENTITY_REGISTRY],
        "security_domains": [{"key": s.key, "label": s.label, "category": s.category,
                              "authoritative_owner": s.authoritative_owner, "provider_owner": s.provider_owner,
                              "monitoring_owner": s.monitoring_owner, "runtime_gate": s.runtime_gate,
                              "deep_links": list(s.deep_links)} for s in registry.SECURITY_REGISTRY],
        "panels": [{"key": p.key, "owner": p.owner, "source": p.source, "measure": p.measure,
                    "permission": p.permission, "deep_link": p.deep_link, "explainability": p.explainability}
                   for p in registry.PANEL_REGISTRY],
        "dashboards": [{"key": d.key, "audience": d.audience, "runtime_gate": d.runtime_gate,
                        "panels": list(d.panels), "required_capabilities": list(d.required_capabilities),
                        "navigation": d.navigation, "governing_services": list(d.governing_services)}
                       for d in registry.SECURITY_DASHBOARDS],
        "coverage": registry.coverage()})


@router.get("/api/v1/security-operations/panel/{key}")
def api_security_panel(key: str, principal: Principal = Depends(require_capability("security.view"))):
    """Compose a single security panel (JSON). 404 when not registered."""
    result = get_panel(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/security-operations/metrics")
def api_security_metrics(principal: Principal = Depends(require_capability("security.view"))):
    """Low-cardinality security-operations-layer metrics (JSON)."""
    return JSONResponse(security_operations_metrics(principal))


@router.get("/security-operations/diagnostics")
def security_diag(principal: Principal = Depends(require_capability("observability.audit"))):
    """Internal-only security-operations diagnostics (registry coverage, panel availability, governance)."""
    return JSONResponse(security_diagnostics())
