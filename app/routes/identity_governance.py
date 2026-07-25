"""Enterprise Identity, Access Governance & Authorization Intelligence routes (Phase D.65).

A governed COMPOSITION surface over the platform's authoritative identity / role / capability / authentication /
authorization owners — the Identity service (`list_identity_data`), Security RBAC (role & capability
resolution, authorization policies), Security Authentication (providers), the Policy engine (policy coverage),
and Security Authorization (record-scope decisions). Reads only — no second identity provider / authentication
service / authorization engine / RBAC system / directory / SSO platform / policy engine / user-management
platform, no mutation (never authenticate / authorize / assign a role / grant / revoke / modify a policy /
create an identity / create a session / manage a password). Routes are gated by ``identity.manage`` OR
``analytics.executive``; each panel additionally self-restricts to its authoritative-source capability.
Diagnostics is gated by ``observability.audit``. No passwords, secrets, tokens, session IDs, credentials,
authentication payloads, raw identities, privileged-role membership, or user-level permission maps are ever
returned — counts, coverage, status, and ratios only. A capability inventory is not a grant; coverage is not
certification.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_any_capability
from app.security.models import Principal
from app.services.identity_governance import (
    compose_dashboard,
    get_panel,
    identity_summary,
    list_dashboards,
)
from app.services.identity_governance.diagnostics import identity_diagnostics
from app.services.identity_governance.metrics import identity_governance_metrics

router = APIRouter(tags=["identity-governance"])
templates = Jinja2Templates(directory="app/templates")

# Identity governance is a sensitive administration / executive surface — either capability may open it.
_IG_GATE = require_any_capability("identity.manage", "analytics.executive")


@router.get("/identity-governance", response_class=HTMLResponse)
def identity_home(request: Request, dashboard: str | None = None,
                  principal: Principal = Depends(_IG_GATE)):
    """The identity-governance dashboard (HTML). Renders the requested dashboard, or the first available."""
    accessible = list_dashboards(principal).get("dashboards", [])
    keys = [d["key"] for d in accessible]
    chosen = dashboard if dashboard in keys else (keys[0] if keys else None)
    result = compose_dashboard(principal, chosen) if chosen else None
    return templates.TemplateResponse(request=request, name="identity_governance/home.html",
                                      context={"result": result, "dashboards": accessible, "chosen": chosen,
                                               "summary": identity_summary(principal)})


@router.get("/api/v1/identity-governance/dashboards")
def api_identity_dashboards(principal: Principal = Depends(_IG_GATE)):
    """The identity dashboards the principal may open (JSON, metadata only)."""
    return JSONResponse(list_dashboards(principal))


@router.get("/api/v1/identity-governance/dashboard/{key}")
def api_identity_dashboard(key: str, principal: Principal = Depends(_IG_GATE)):
    """Compose a named identity dashboard (JSON). 404 when not registered or the principal lacks its required
    capability."""
    result = compose_dashboard(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/identity-governance/summary")
def api_identity_summary(principal: Principal = Depends(_IG_GATE)):
    """The firm identity / access-governance summary (JSON) — compact, non-leaking. Backs the workspace panel +
    the Client 360 / Household 360 sections + AI grounding. Governance coverage only — a capability inventory is
    not a grant, coverage is not certification."""
    return JSONResponse(identity_summary(principal))


@router.get("/api/v1/identity-governance/registry")
def api_identity_registry(principal: Principal = Depends(_IG_GATE)):
    """The identity + role + capability + authentication + authorization + panel + dashboard registries (JSON)
    — the declarative catalogs. Metadata only — never a raw identity or permission map."""
    from app.services.identity_governance import registry

    def _entries(reg):
        return [{"key": e.key, "label": e.label, "owner": e.owner, "runtime_gate": e.runtime_gate,
                 "capabilities": list(e.capabilities), "identity_scope": e.identity_scope,
                 "deep_links": list(e.deep_links), "config_status": e.config_status} for e in reg]
    return JSONResponse({
        "identity_domains": _entries(registry.IDENTITY_REGISTRY),
        "role_domains": _entries(registry.ROLE_REGISTRY),
        "capability_domains": _entries(registry.CAPABILITY_REGISTRY),
        "authentication_domains": _entries(registry.AUTHENTICATION_REGISTRY),
        "authorization_domains": _entries(registry.AUTHORIZATION_REGISTRY),
        "panels": [{"key": p.key, "owner": p.owner, "source": p.source, "measure": p.measure,
                    "permission": p.permission, "deep_link": p.deep_link, "derived": p.derived,
                    "explainability": p.explainability} for p in registry.PANEL_REGISTRY],
        "dashboards": [{"key": d.key, "audience": d.audience, "runtime_gate": d.runtime_gate,
                        "panels": list(d.panels), "required_capabilities": list(d.required_capabilities),
                        "navigation": d.navigation, "governing_services": list(d.governing_services)}
                       for d in registry.IDENTITY_DASHBOARDS],
        "coverage": registry.coverage()})


@router.get("/api/v1/identity-governance/panel/{key}")
def api_identity_panel(key: str, principal: Principal = Depends(_IG_GATE)):
    """Compose a single identity panel (JSON). 404 when not registered."""
    result = get_panel(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/identity-governance/metrics")
def api_identity_metrics(principal: Principal = Depends(_IG_GATE)):
    """Low-cardinality identity-governance-layer metrics (JSON)."""
    return JSONResponse(identity_governance_metrics(principal))


@router.get("/identity-governance/diagnostics")
def identity_diag(principal: Principal = Depends(require_any_capability("observability.audit"))):
    """Internal-only identity-governance diagnostics (registry coverage, panel availability, governance)."""
    return JSONResponse(identity_diagnostics())
