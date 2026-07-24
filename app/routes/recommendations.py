"""Enterprise Operational Intelligence routes (Phase D.46).

A governed COMPOSITION surface over the authoritative recommendation sources — no second recommendation/
workflow/opportunity engine, no ML. Reads only. The client/household recommendations, workspace panel,
summary, and explanation all enforce record scope via the composition (out-of-scope → the service returns
None → 404); reads are gated by ``client.read`` and diagnostics by ``observability.audit``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.recommendations import (
    client_recommendations,
    explain_recommendation,
    household_recommendations,
    workspace_recommendations,
)
from app.services.recommendations.diagnostics import recommendation_diagnostics
from app.services.recommendations.metrics import recommendation_metrics

router = APIRouter(tags=["recommendations"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/recommendations", response_class=HTMLResponse)
def recommendations_home(request: Request, person_id: int | None = None, household_id: int | None = None,
                         principal: Principal = Depends(require_capability("client.read"))):
    """The explainable recommendations surface — a client/household view, or the workspace panel (HTML)."""
    if person_id is not None:
        result = client_recommendations(principal, person_id)
        if result is None:
            raise HTTPException(404, "Not found")
        anchor = f"person:{person_id}"
    elif household_id is not None:
        result = household_recommendations(principal, household_id)
        if result is None:
            raise HTTPException(404, "Not found")
        anchor = f"household:{household_id}"
    else:
        result = workspace_recommendations(principal)
        anchor = None
    return templates.TemplateResponse(request=request, name="recommendations/home.html",
                                      context={"result": result, "anchor": anchor})


@router.get("/api/v1/recommendations")
def api_recommendations(person_id: int | None = None, household_id: int | None = None,
                        principal: Principal = Depends(require_capability("client.read"))):
    """Explainable recommendations for a client or household (JSON). 404 when out of scope."""
    if person_id is not None:
        result = client_recommendations(principal, person_id)
    elif household_id is not None:
        result = household_recommendations(principal, household_id)
    else:
        return JSONResponse(workspace_recommendations(principal))
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/recommendations/workspace")
def api_recommendations_workspace(principal: Principal = Depends(require_capability("client.read"))):
    """The Advisor Workspace Operational Intelligence panel (JSON) — highest-priority recommendations +
    domain observations + workload distribution."""
    return JSONResponse(workspace_recommendations(principal))


@router.get("/api/v1/recommendations/summary")
def api_recommendations_summary(person_id: int | None = None, household_id: int | None = None,
                                principal: Principal = Depends(require_capability("client.read"))):
    """Compact recommendation summary (JSON) — counts + top. Backs the C360/HH360 sections + AI grounding."""
    from app.services.recommendations import recommendation_summary
    return JSONResponse(recommendation_summary(principal, person_id=person_id, household_id=household_id))


@router.get("/api/v1/recommendations/{recommendation_id}/explain")
def api_recommendations_explain(recommendation_id: str, person_id: int | None = None,
                                household_id: int | None = None,
                                principal: Principal = Depends(require_capability("client.read"))):
    """Explain one recommendation (JSON) — why/rule/sources/evidence/workflow-owner/deep-link. 404 out of scope."""
    result = explain_recommendation(principal, recommendation_id, person_id=person_id,
                                    household_id=household_id)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/recommendations/metrics")
def api_recommendations_metrics(principal: Principal = Depends(require_capability("client.read"))):
    """Low-cardinality recommendation metrics (JSON)."""
    return JSONResponse(recommendation_metrics(principal))


@router.get("/recommendations/diagnostics")
def recommendations_diag(principal: Principal = Depends(require_capability("observability.audit"))):
    """Internal-only recommendation diagnostics (registry coverage, adapter availability, governance)."""
    return JSONResponse(recommendation_diagnostics())
