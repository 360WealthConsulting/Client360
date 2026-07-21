"""Client / household activity timeline routes (Phase D.10).

Read-only projection over existing domains. Gated by ``timeline.read`` (route-level) and
person/household record scope (the ``^/(people|households)/(\\d+)`` middleware RECORD_PATH
covers these paths; the service also re-checks scope). No mutation, no inline actions, no
bulk actions. Redaction is decided in the service, not the template.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.activity_timeline import service as svc
from app.templating import install_filters

router = APIRouter(tags=["timeline"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)  # humandt for event timestamps


def _context(request, result, *, scope_label, back_url):
    return {
        "request": request, "result": result, "scope_label": scope_label, "back_url": back_url,
        "filters": {
            "event_type": request.query_params.get("event_type", ""),
            "source": request.query_params.get("source", ""),
            "date_from": request.query_params.get("date_from", ""),
            "date_to": request.query_params.get("date_to", ""),
            "q": request.query_params.get("q", ""),
        },
        "source_domains": svc.SOURCE_DOMAINS,
    }


@router.get("/people/{person_id}/timeline", response_class=HTMLResponse)
def client_timeline(
    request: Request, person_id: int,
    event_type: str | None = None, source: str | None = None,
    date_from: str | None = None, date_to: str | None = None, q: str | None = None,
    page: int = 1,
    principal: Principal = Depends(require_capability("timeline.read")),
):
    result = svc.client_timeline(
        principal, person_id, event_type=event_type, source_domain=source,
        date_from=date_from, date_to=date_to, search=q, page=page)
    if result is None:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(
        request=request, name="activity_timeline/timeline.html",
        context=_context(request, result, scope_label=f"Person {person_id}",
                         back_url=f"/people/{person_id}"))


@router.get("/households/{household_id}/timeline", response_class=HTMLResponse)
def household_timeline(
    request: Request, household_id: int,
    event_type: str | None = None, source: str | None = None,
    date_from: str | None = None, date_to: str | None = None, q: str | None = None,
    page: int = 1,
    principal: Principal = Depends(require_capability("timeline.read")),
):
    result = svc.household_timeline(
        principal, household_id, event_type=event_type, source_domain=source,
        date_from=date_from, date_to=date_to, search=q, page=page)
    if result is None:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(
        request=request, name="activity_timeline/timeline.html",
        context=_context(request, result, scope_label=f"Household {household_id}",
                         back_url=f"/households/{household_id}"))
