"""Enterprise Regulatory Examination Readiness, Evidence Governance & Supervisory Certification routes
(Phase D.59).

A governed COMPOSITION surface over the platform's authoritative regulatory / evidence / certification owners —
Compliance Intelligence + `compliance/reviews` + the rule catalog + the reviewer-authority owner, the
Exception Engine, Document Intelligence, Data Governance, Security Operations, Business Continuity, Vendor
Management, Financial Operations, Insurance licensing, audit logging, and the CI pipeline. Reads only — no
second compliance / examination / audit / document / filing / certification platform, no mutation. Routes are
gated by ``compliance.supervise`` OR ``analytics.executive``; each panel additionally self-restricts to its
authoritative-source capability. Diagnostics is gated by ``observability.audit``. No document contents,
regulator correspondence, client narratives, tax-return data, credentials, tokens, account numbers, filing
payloads, or protected supervisory details are ever returned — counts, status, coverage, freshness, and age
bands only. Operational readiness is never regulatory certification.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_any_capability
from app.security.models import Principal
from app.services.regulatory_readiness import (
    compose_dashboard,
    get_panel,
    list_dashboards,
    readiness_summary,
)
from app.services.regulatory_readiness.diagnostics import readiness_diagnostics
from app.services.regulatory_readiness.metrics import regulatory_readiness_metrics

router = APIRouter(tags=["regulatory-readiness"])
templates = Jinja2Templates(directory="app/templates")

# Regulatory readiness is a supervisory / executive oversight surface — either capability may open it.
_RR_GATE = require_any_capability("compliance.supervise", "analytics.executive")


@router.get("/regulatory-readiness", response_class=HTMLResponse)
def readiness_home(request: Request, dashboard: str | None = None,
                   principal: Principal = Depends(_RR_GATE)):
    """The regulatory-readiness dashboard (HTML). Renders the requested dashboard, or the first available."""
    accessible = list_dashboards(principal).get("dashboards", [])
    keys = [d["key"] for d in accessible]
    chosen = dashboard if dashboard in keys else (keys[0] if keys else None)
    result = compose_dashboard(principal, chosen) if chosen else None
    return templates.TemplateResponse(request=request, name="regulatory_readiness/home.html",
                                      context={"result": result, "dashboards": accessible, "chosen": chosen,
                                               "summary": readiness_summary(principal)})


@router.get("/api/v1/regulatory-readiness/dashboards")
def api_readiness_dashboards(principal: Principal = Depends(_RR_GATE)):
    """The readiness dashboards the principal may open (JSON, metadata only)."""
    return JSONResponse(list_dashboards(principal))


@router.get("/api/v1/regulatory-readiness/dashboard/{key}")
def api_readiness_dashboard(key: str, principal: Principal = Depends(_RR_GATE)):
    """Compose a named readiness dashboard (JSON). 404 when not registered or the principal lacks its required
    capability."""
    result = compose_dashboard(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/regulatory-readiness/summary")
def api_readiness_summary(principal: Principal = Depends(_RR_GATE)):
    """The firm regulatory-readiness summary (JSON) — compact, non-leaking. Backs the workspace panel + the
    Client 360 / Household 360 sections + AI grounding. Operational readiness is not regulatory
    certification."""
    return JSONResponse(readiness_summary(principal))


@router.get("/api/v1/regulatory-readiness/registry")
def api_readiness_registry(principal: Principal = Depends(_RR_GATE)):
    """The obligation + evidence + examination-request + certification + panel + dashboard registries (JSON)."""
    from app.services.regulatory_readiness import registry
    return JSONResponse({
        "obligations": [{"key": o.key, "label": o.label, "reg_domain": o.reg_domain,
                         "authoritative_owner": o.authoritative_owner, "evidence_owner": o.evidence_owner,
                         "review_owner": o.review_owner, "approval_owner": o.approval_owner,
                         "filing_owner": o.filing_owner, "retention_owner": o.retention_owner,
                         "business_owner": o.business_owner, "compliance_reviewer": o.compliance_reviewer,
                         "capabilities": list(o.capabilities), "runtime_gate": o.runtime_gate,
                         "deep_links": list(o.deep_links), "config_status": o.config_status}
                        for o in registry.REGULATORY_OBLIGATION_REGISTRY],
        "evidence_classes": [{"key": e.key, "evidence_class": e.evidence_class,
                              "authoritative_owner": e.authoritative_owner, "storage_owner": e.storage_owner,
                              "verification_owner": e.verification_owner, "retention_owner": e.retention_owner,
                              "obligation_keys": list(e.obligation_keys), "freshness": e.freshness,
                              "capabilities": list(e.capabilities), "deep_link": e.deep_link,
                              "config_status": e.config_status} for e in registry.EVIDENCE_REGISTRY],
        "examination_requests": [{"key": r.key, "category": r.category, "description": r.description,
                                  "required_evidence": list(r.required_evidence),
                                  "authoritative_owners": list(r.authoritative_owners),
                                  "review_owner": r.review_owner, "export_owner": r.export_owner,
                                  "capabilities": list(r.capabilities), "deep_links": list(r.deep_links),
                                  "config_status": r.config_status}
                                 for r in registry.EXAMINATION_REQUEST_REGISTRY],
        "certifications": [{"key": c.key, "scope": c.scope, "ruleset_version": c.ruleset_version,
                            "accountable_reviewer_role": c.accountable_reviewer_role,
                            "named_reviewer": c.named_reviewer,
                            "reviewer_qualification": c.reviewer_qualification, "review_date": c.review_date,
                            "status": c.status, "blocked_reason": c.blocked_reason,
                            "evidence_owner": c.evidence_owner,
                            "approval_artifact_owner": c.approval_artifact_owner,
                            "capabilities": list(c.capabilities), "deep_link": c.deep_link,
                            "config_status": c.config_status} for c in registry.CERTIFICATION_REGISTRY],
        "panels": [{"key": p.key, "owner": p.owner, "source": p.source, "measure": p.measure,
                    "permission": p.permission, "deep_link": p.deep_link, "derived": p.derived,
                    "explainability": p.explainability} for p in registry.PANEL_REGISTRY],
        "dashboards": [{"key": d.key, "audience": d.audience, "runtime_gate": d.runtime_gate,
                        "panels": list(d.panels), "required_capabilities": list(d.required_capabilities),
                        "navigation": d.navigation, "governing_services": list(d.governing_services)}
                       for d in registry.READINESS_DASHBOARDS],
        "coverage": registry.coverage()})


@router.get("/api/v1/regulatory-readiness/panel/{key}")
def api_readiness_panel(key: str, principal: Principal = Depends(_RR_GATE)):
    """Compose a single readiness panel (JSON). 404 when not registered."""
    result = get_panel(principal, key)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/regulatory-readiness/metrics")
def api_readiness_metrics(principal: Principal = Depends(_RR_GATE)):
    """Low-cardinality regulatory-readiness-layer metrics (JSON)."""
    return JSONResponse(regulatory_readiness_metrics(principal))


@router.get("/regulatory-readiness/diagnostics")
def readiness_diag(principal: Principal = Depends(require_any_capability("observability.audit"))):
    """Internal-only regulatory-readiness diagnostics (registry coverage, panel availability, governance)."""
    return JSONResponse(readiness_diagnostics())
