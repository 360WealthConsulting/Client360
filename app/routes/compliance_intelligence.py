"""Enterprise Compliance Intelligence & Supervisory Operations routes (Phase D.47).

A governed COMPOSITION surface over the authoritative compliance/review/exception/audit/approval services —
no second compliance/approval/audit engine. Reads only. EVERY supervisory route is gated by the
``compliance.supervise`` capability (the supervisor-vs-advisor boundary; advisors do not hold it), and the
composition additionally enforces record scope (out-of-scope → the service returns None → 404). Diagnostics
is gated by ``observability.audit``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.compliance_intelligence import (
    client_compliance,
    compliance_summary,
    household_compliance,
    supervisory_dashboard,
)
from app.services.compliance_intelligence.diagnostics import compliance_diagnostics
from app.services.compliance_intelligence.metrics import compliance_metrics

router = APIRouter(tags=["compliance-intelligence"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/supervision", response_class=HTMLResponse)
def supervision_home(request: Request,
                     principal: Principal = Depends(require_capability("compliance.supervise"))):
    """The enterprise supervisory workspace (HTML) — supervisor-only."""
    result = supervisory_dashboard(principal)
    if result is None:
        raise HTTPException(403, "Supervisory access required")
    return templates.TemplateResponse(request=request, name="compliance_intelligence/home.html",
                                      context={"result": result})


@router.get("/api/v1/supervision/dashboard")
def api_supervision_dashboard(principal: Principal = Depends(require_capability("compliance.supervise"))):
    """The enterprise supervisory dashboard (JSON) — supervisor-only."""
    result = supervisory_dashboard(principal)
    if result is None:
        raise HTTPException(403, "Supervisory access required")
    return JSONResponse(result)


@router.get("/api/v1/supervision/client")
def api_supervision_client(person_id: int | None = None, household_id: int | None = None,
                           principal: Principal = Depends(require_capability("compliance.supervise"))):
    """Supervisory compliance view for a client or household (JSON). 404 when out of scope."""
    if person_id is not None:
        result = client_compliance(principal, person_id)
    elif household_id is not None:
        result = household_compliance(principal, household_id)
    else:
        raise HTTPException(400, "person_id or household_id required")
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/supervision/summary")
def api_supervision_summary(person_id: int | None = None, household_id: int | None = None,
                            principal: Principal = Depends(require_capability("compliance.supervise"))):
    """Compact supervisory summary (JSON) — counts only. Backs the C360/HH360 sections + AI grounding."""
    return JSONResponse(compliance_summary(principal, person_id=person_id, household_id=household_id))


@router.get("/api/v1/supervision/registry")
def api_supervision_registry(principal: Principal = Depends(require_capability("compliance.supervise"))):
    """The supervisory review + exception registries (JSON) — the declarative catalogs."""
    from app.services.compliance_intelligence import registry
    return JSONResponse({
        "review_types": [{"key": t.key, "owner": t.owner, "governing_workflow": t.governing_workflow,
                          "policy_owner": t.policy_owner, "approval_authority": t.approval_authority,
                          "escalation_path": t.escalation_path, "retention_class": t.retention_class,
                          "deep_link": t.deep_link, "runtime_gate": t.runtime_gate,
                          "populated": t.populated} for t in registry.SUPERVISORY_REGISTRY],
        "exception_types": [{"key": t.key, "owner": t.owner, "default_severity": t.default_severity,
                             "governing_policy": t.governing_policy, "escalation": t.escalation}
                            for t in registry.EXCEPTION_REGISTRY],
        "coverage": registry.coverage()})


@router.get("/api/v1/supervision/metrics")
def api_supervision_metrics(principal: Principal = Depends(require_capability("compliance.supervise"))):
    """Low-cardinality supervisory metrics (JSON)."""
    return JSONResponse(compliance_metrics(principal))


@router.get("/supervision/diagnostics")
def supervision_diag(principal: Principal = Depends(require_capability("observability.audit"))):
    """Internal-only supervisory diagnostics (registry coverage, adapter availability, governance)."""
    return JSONResponse(compliance_diagnostics())
