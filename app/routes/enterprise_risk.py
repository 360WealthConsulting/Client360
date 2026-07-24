"""Enterprise Risk Management, Internal Controls & Assurance Governance routes (Phase D.58).

A governed COMPOSITION surface over the platform's authoritative risk / control / assurance owners — Compliance
Intelligence + the Exception Engine, Security Operations + incidents, Data Governance, the Integration
Platform, Business Continuity, Vendor Management, Financial Operations, Document Intelligence, Automation
Orchestration, Insurance licensing, and the Runtime + Policy engines + audit logging. Reads only — no second
GRC platform / risk register / compliance engine / exception system / audit platform / incident-management
system / control-testing application / policy engine / approval engine, no mutation. Routes are gated by
``compliance.supervise`` OR ``analytics.executive``; each panel additionally self-restricts to its own
authoritative-source capability. Diagnostics is gated by ``observability.audit``. No client-sensitive evidence,
audit payloads, security details, credentials, tokens, bank information, tax-return contents, document
contents, or private incident narratives are ever returned — counts, status, severity distributions, and
coverage summaries only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_any_capability
from app.security.models import Principal
from app.services.enterprise_risk import (
    compose_dashboard,
    get_panel,
    list_dashboards,
    risk_summary,
)
from app.services.enterprise_risk.diagnostics import risk_diagnostics
from app.services.enterprise_risk.metrics import enterprise_risk_metrics

router = APIRouter(tags=["enterprise-risk"])
templates = Jinja2Templates(directory="app/templates")

# Enterprise risk is a supervisory / executive oversight surface — either capability may open it.
_RISK_GATE = require_any_capability("compliance.supervise", "analytics.executive")


@router.get("/enterprise-risk", response_class=HTMLResponse)
def risk_home(request: Request, dashboard: str | None = None,
              principal: Principal = Depends(_RISK_GATE)):
    """The enterprise-risk dashboard (HTML). Renders the requested dashboard, or the first available."""
    accessible = list_dashboards(principal).get("dashboards", [])
    keys = [d["key"] for d in accessible]
    chosen = dashboard if dashboard in keys else (keys[0] if keys else None)
    result = compose_dashboard(principal, chosen) if chosen else None
    return templates.TemplateResponse(request=request, name="enterprise_risk/home.html",
                                      context={"result": result, "dashboards": accessible, "chosen": chosen,
                                               "summary": risk_summary(principal)})


@router.get("/api/v1/enterprise-risk/dashboards")
def api_risk_dashboards(principal: Principal = Depends(_RISK_GATE)):
    """The risk dashboards the principal may open (JSON, metadata only)."""
    return JSONResponse(list_dashboards(principal))


@router.get("/api/v1/enterprise-risk/dashboard/{key}")
def api_risk_dashboard(key: str, principal: Principal = Depends(_RISK_GATE)):
    """Compose a named risk dashboard (JSON). 404 when not registered or the principal lacks its required
    capability."""
    result = compose_dashboard(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/enterprise-risk/summary")
def api_risk_summary(principal: Principal = Depends(_RISK_GATE)):
    """The firm enterprise-risk & controls summary (JSON) — compact, non-leaking. Backs the workspace panel +
    the Client 360 / Household 360 sections + AI grounding."""
    return JSONResponse(risk_summary(principal))


@router.get("/api/v1/enterprise-risk/registry")
def api_risk_registry(principal: Principal = Depends(_RISK_GATE)):
    """The risk + control + assurance + panel + dashboard registries (JSON) — the declarative catalogs."""
    from app.services.enterprise_risk import registry
    return JSONResponse({
        "risk_domains": [{"key": r.key, "label": r.label, "risk_category": r.risk_category,
                          "authoritative_owner": r.authoritative_owner,
                          "signal_owners": list(r.signal_owners), "exception_owner": r.exception_owner,
                          "incident_owner": r.incident_owner, "remediation_owner": r.remediation_owner,
                          "assurance_owner": r.assurance_owner, "capabilities": list(r.capabilities),
                          "runtime_gate": r.runtime_gate, "deep_links": list(r.deep_links),
                          "config_status": r.config_status} for r in registry.ENTERPRISE_RISK_REGISTRY],
        "control_families": [{"key": c.key, "control_family": c.control_family,
                              "control_objective": c.control_objective,
                              "authoritative_owner": c.authoritative_owner, "evidence_owner": c.evidence_owner,
                              "monitoring_owner": c.monitoring_owner, "test_owner": c.test_owner,
                              "approval_owner": c.approval_owner, "remediation_owner": c.remediation_owner,
                              "runtime_gate": c.runtime_gate, "capabilities": list(c.capabilities),
                              "deep_links": list(c.deep_links), "config_status": c.config_status}
                             for c in registry.CONTROL_REGISTRY],
        "assurance_sources": [{"key": a.key, "label": a.label, "assurance_owner": a.assurance_owner,
                               "evidence_source": a.evidence_source, "scope": a.scope,
                               "frequency": a.frequency, "reviewer_role": a.reviewer_role,
                               "approval_artifact": a.approval_artifact, "runtime_gate": a.runtime_gate,
                               "capabilities": list(a.capabilities), "deep_link": a.deep_link,
                               "config_status": a.config_status} for a in registry.ASSURANCE_REGISTRY],
        "panels": [{"key": p.key, "owner": p.owner, "source": p.source, "measure": p.measure,
                    "permission": p.permission, "deep_link": p.deep_link, "derived": p.derived,
                    "explainability": p.explainability} for p in registry.PANEL_REGISTRY],
        "dashboards": [{"key": d.key, "audience": d.audience, "runtime_gate": d.runtime_gate,
                        "panels": list(d.panels), "required_capabilities": list(d.required_capabilities),
                        "navigation": d.navigation, "governing_services": list(d.governing_services)}
                       for d in registry.RISK_DASHBOARDS],
        "coverage": registry.coverage()})


@router.get("/api/v1/enterprise-risk/panel/{key}")
def api_risk_panel(key: str, principal: Principal = Depends(_RISK_GATE)):
    """Compose a single risk panel (JSON). 404 when not registered."""
    result = get_panel(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/enterprise-risk/metrics")
def api_risk_metrics(principal: Principal = Depends(_RISK_GATE)):
    """Low-cardinality enterprise-risk-layer metrics (JSON)."""
    return JSONResponse(enterprise_risk_metrics(principal))


@router.get("/enterprise-risk/diagnostics")
def risk_diag(principal: Principal = Depends(require_any_capability("observability.audit"))):
    """Internal-only enterprise-risk diagnostics (registry coverage, panel availability, governance)."""
    return JSONResponse(risk_diagnostics())
