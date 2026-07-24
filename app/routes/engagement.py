"""Unified Communications & Engagement routes (Phase D.44).

A governed COMPOSITION surface over authoritative communication subsystems — no second messaging/timeline/
notification store. Reads only. The person/household engagement timeline reuses the authoritative activity
timeline (record-scoped, deduped) and classifies it onto registered interaction types; the JSON summary +
search endpoints back the advisor surfaces + AI grounding; diagnostics is internal-only.

Gated by ``communications.view`` (reads) and ``observability.audit`` (diagnostics). Record scope is enforced
by the underlying composition (out-of-scope → 404).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.communications.engagement import (
    engagement_summary,
    engagement_timeline,
    search_interactions,
)
from app.services.communications.engagement.diagnostics import engagement_diagnostics
from app.services.communications.engagement.metrics import engagement_metrics
from app.templating import install_filters

router = APIRouter(tags=["engagement"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


def _bool(v):
    if v is None:
        return None
    return str(v).lower() in ("1", "true", "yes", "on")


@router.get("/engagement", response_class=HTMLResponse)
def engagement_home(
    request: Request, person_id: int | None = None, household_id: int | None = None,
    interaction_type: str | None = None, q: str | None = None, page: int = 1,
    principal: Principal = Depends(require_capability("communications.view")),
):
    """Unified engagement timeline for a person or household (HTML)."""
    if person_id is None and household_id is None:
        # Landing view — the surface needs a client/household anchor to compose a scoped timeline.
        return templates.TemplateResponse(request=request, name="engagement/home.html",
                                          context={"result": None, "anchor": None})
    result = engagement_timeline(principal, person_id=person_id, household_id=household_id,
                                 interaction_type=interaction_type, search=q, page=page)
    if result is None:
        raise HTTPException(404, "Not found")
    anchor = f"person:{person_id}" if person_id else f"household:{household_id}"
    return templates.TemplateResponse(request=request, name="engagement/home.html",
                                      context={"result": result, "anchor": anchor,
                                               "filters": {"interaction_type": interaction_type or "", "q": q or ""}})


@router.get("/api/v1/engagement/timeline")
def api_engagement_timeline(
    person_id: int | None = None, household_id: int | None = None, interaction_type: str | None = None,
    event_type: str | None = None, date_from: str | None = None, date_to: str | None = None,
    q: str | None = None, unread: str | None = None, action_required: str | None = None,
    has_attachment: str | None = None, visibility: str | None = None, direction: str | None = None,
    source: str | None = None, page: int = 1, page_size: int = 25,
    principal: Principal = Depends(require_capability("communications.view")),
):
    """Unified engagement timeline (JSON). 404 when out of scope."""
    result = engagement_timeline(principal, person_id=person_id, household_id=household_id,
                                 interaction_type=interaction_type, event_type=event_type,
                                 date_from=date_from, date_to=date_to, search=q, unread=_bool(unread),
                                 action_required=_bool(action_required), has_attachment=_bool(has_attachment),
                                 visibility=visibility, direction=direction, source=source,
                                 page=page, page_size=page_size)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/engagement/search")
def api_engagement_search(
    q: str, person_id: int | None = None, household_id: int | None = None,
    interaction_type: str | None = None, unread: str | None = None, action_required: str | None = None,
    has_attachment: str | None = None, visibility: str | None = None, direction: str | None = None,
    source: str | None = None, page: int = 1, page_size: int = 25,
    principal: Principal = Depends(require_capability("communications.view")),
):
    """Unified communication search (JSON). Delegates text match + scope to the authoritative timeline."""
    result = search_interactions(principal, person_id=person_id, household_id=household_id, query=q,
                                 interaction_type=interaction_type, unread=_bool(unread),
                                 action_required=_bool(action_required), has_attachment=_bool(has_attachment),
                                 visibility=visibility, direction=direction, source=source,
                                 page=page, page_size=page_size)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/api/v1/engagement/summary")
def api_engagement_summary(
    person_id: int | None = None, household_id: int | None = None,
    principal: Principal = Depends(require_capability("communications.view")),
):
    """Compact engagement summary (JSON) — counts + last interaction. Backs the advisor surfaces + AI."""
    return JSONResponse(engagement_summary(principal, person_id=person_id, household_id=household_id))


@router.get("/api/v1/engagement/metrics")
def api_engagement_metrics(principal: Principal = Depends(require_capability("communications.view"))):
    """Low-cardinality engagement metrics (JSON)."""
    return JSONResponse(engagement_metrics(principal))


@router.get("/engagement/diagnostics")
def engagement_diag(principal: Principal = Depends(require_capability("observability.audit"))):
    """Internal-only engagement diagnostics (adapter availability, registry coverage, governance)."""
    return JSONResponse(engagement_diagnostics())
