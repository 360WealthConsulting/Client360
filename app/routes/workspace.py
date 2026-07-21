from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.authorization import record_in_scope
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.advisor_intelligence import get_client_signals, get_dashboard_signals
from app.services.advisor_workspace import (
    get_daily_dashboard,
    get_meeting_brief,
    get_meeting_outcome_context,
    record_meeting_outcome,
)

router = APIRouter(prefix="/workspace", tags=["workspace"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
def workspace_dashboard(
    request: Request,
    principal: Principal = Depends(require_capability("client.read")),
):
    """Advisor Workspace — read-only Daily Dashboard (Phase D.1).

    Book-scoped (NOT a firm-wide collection): the route requires `client.read`
    and the orchestration service scopes every panel to the advisor's accessible
    clients via `accessible_person_ids`. Composition only — no writes, no new
    domain/task/workflow/exception/notification logic, no advisor intelligence.
    """
    dashboard = get_daily_dashboard(principal)
    # Advisor Intelligence framework (Phase D.5A) — book-scoped, deterministic,
    # propose-only. Returns () in this phase (no rules registered); the panel
    # renders its placeholder empty state. No recommendations, no AI.
    signals = get_dashboard_signals(principal)
    return templates.TemplateResponse(
        request=request,
        name="workspace/dashboard.html",
        context={"principal": principal, "d": dashboard, "signals": signals},
    )


@router.get("/meetings/{person_id}", response_class=HTMLResponse)
def meeting_brief(
    request: Request,
    person_id: int,
    event: int | None = None,
    principal: Principal = Depends(require_capability("client.read")),
):
    """Meeting Workspace — read-only meeting-preparation brief for one client
    (Phase D.3). `/workspace/meetings/{id}` is NOT covered by the middleware
    RECORD_PATH, so this route enforces person record-scope explicitly (404 for an
    inaccessible person, matching the person-profile behavior). The optional
    `event` id is validated in the service to belong to this person and to be a
    calendar event; otherwise a general brief is rendered.
    """
    if not record_in_scope(principal, "person", person_id):
        raise HTTPException(404, "Not found")
    brief = get_meeting_brief(person_id, event_id=event)
    if brief is None:
        raise HTTPException(404, "Not found")
    # Advisor Intelligence (Phase D.5C): reuse the single scoped signal producer for
    # this in-scope client. No signal generation in the template.
    signals = get_client_signals(principal, person_id)
    return templates.TemplateResponse(
        request=request,
        name="workspace/meeting_brief.html",
        context={"principal": principal, "b": brief, "signals": signals},
    )


@router.get("/meetings/{person_id}/outcome", response_class=HTMLResponse)
def meeting_outcome_form(
    request: Request,
    person_id: int,
    event: int | None = None,
    principal: Principal = Depends(require_capability("client.read")),
):
    """Meeting Outcome workspace (Phase D.4) — read-only context + the editable
    outcome form. Person record-scope is enforced explicitly (RECORD_PATH does not
    cover /workspace/meetings/{id}/outcome)."""
    if not record_in_scope(principal, "person", person_id):
        raise HTTPException(404, "Not found")
    context = get_meeting_outcome_context(person_id, event_id=event)
    if context is None:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(
        request=request,
        name="workspace/meeting_outcome.html",
        context={"principal": principal, "b": context,
                 "saved": request.query_params.get("saved") == "1"},
    )


@router.post("/meetings/{person_id}/outcome")
async def meeting_outcome_submit(
    request: Request,
    person_id: int,
    principal: Principal = Depends(require_capability("client.write")),
):
    """Record factual meeting outcomes, transitioning agreed work into the existing
    platform via authoritative services. Requires client.write AND person WRITE
    record-scope. No new engine/model — see advisor_workspace.record_meeting_outcome."""
    if not record_in_scope(principal, "person", person_id, write=True):
        raise HTTPException(404, "Not found")
    form = parse_qs((await request.body()).decode("utf-8"))

    def _one(key):
        return form.get(key, [""])[0].strip()

    record_meeting_outcome(
        person_id,
        actor_user_id=principal.user_id,
        completed=_one("completed") in ("on", "true", "1"),
        meeting_notes=_one("meeting_notes"),
        decisions=_one("decisions"),
        comments=_one("comments"),
        follow_ups=form.get("follow_up", []),
        next_review_code=_one("next_review") or None,
        request_id=getattr(request.state, "request_id", None),
    )
    return RedirectResponse(url=f"/workspace/meetings/{person_id}/outcome?saved=1", status_code=303)
