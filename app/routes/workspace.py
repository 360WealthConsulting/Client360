from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.authorization import record_in_scope
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import advisor_work
from app.services.advisor_intelligence import (
    get_client_signals,
    get_dashboard_signals,
    group_signals,
)
from app.services.advisor_workspace import (
    get_meeting_brief,
    get_meeting_outcome_context,
    record_meeting_outcome,
)
from app.services.workspace import get_workspace, preferences, summaries

router = APIRouter(prefix="/workspace", tags=["workspace"])
templates = Jinja2Templates(directory="app/templates")
# Grouping for the shared Advisor Intelligence renderer lives in Python (D.5E).
templates.env.globals["signal_groups"] = group_signals

_MOVE = {"move_up": ("up",), "move_down": ("down",)}


async def _form(request):
    from urllib.parse import parse_qs
    form = parse_qs((await request.body()).decode("utf-8"))
    return lambda key: form.get(key, [""])[0].strip()


@router.get("", response_class=HTMLResponse)
def workspace_dashboard(
    request: Request,
    principal: Principal = Depends(require_capability("client.read")),
):
    """Advisor Workspace — the personalized advisor home (Phase D.38, extends Phase D.1).

    Book-scoped (NOT a firm-wide collection): the route requires `client.read`
    and every read is scoped to the advisor's accessible clients (RBAC preserved).
    Composition + personalization only — no writes, no business logic. Widgets read
    from the D.36/D.37 projections when healthy+fresh, else fall back to the
    authoritative record-scoped read (behavior unchanged by default).
    """
    ws = get_workspace(principal)
    # Advisor Intelligence framework (Phase D.5A) — book-scoped, deterministic,
    # propose-only. Returns () in this phase (no rules registered).
    signals = get_dashboard_signals(principal)
    return templates.TemplateResponse(
        request=request,
        name="workspace/dashboard.html",
        context={"principal": principal, "ws": ws, "d": ws["daily"], "signals": signals,
                 "customize": request.query_params.get("customize") == "1"},
    )


@router.post("/customize")
async def customize(
    request: Request,
    principal: Principal = Depends(require_capability("workspace.personalize")),
):
    """Personalize the workspace layout (reorder / hide / show / pin / unpin one widget). Self-service:
    only the acting user's own preferences are touched. POST-redirect-GET back to the workspace."""
    f = await _form(request)
    action, key = f("action"), f("key")
    uid = principal.user_id
    if action in _MOVE:
        preferences.move_widget(uid, key, *_MOVE[action])
    elif action == "hide":
        preferences.hide_widget(uid, key)
    elif action == "show":
        preferences.show_widget(uid, key)
    elif action == "pin":
        preferences.pin_widget(uid, key)
    elif action == "unpin":
        preferences.unpin_widget(uid, key)
    return RedirectResponse(url="/workspace?customize=1", status_code=303)


@router.post("/presets")
async def presets(
    request: Request,
    principal: Principal = Depends(require_capability("workspace.personalize")),
):
    """Save / apply / delete a named layout preset (self-service, own presets only)."""
    f = await _form(request)
    action, uid = f("action"), principal.user_id
    if action == "save":
        preferences.save_preset(uid, f("name"))
    elif action == "apply" and f("preset_id"):
        preferences.apply_preset(uid, int(f("preset_id")))
    elif action == "delete" and f("preset_id"):
        preferences.delete_preset(uid, int(f("preset_id")))
    return RedirectResponse(url="/workspace?customize=1", status_code=303)


@router.post("/reset")
async def reset_layout(
    principal: Principal = Depends(require_capability("workspace.personalize")),
):
    """Reset the workspace layout to the registry defaults (self-service)."""
    preferences.reset(principal.user_id)
    return RedirectResponse(url="/workspace?customize=1", status_code=303)


@router.get("/summaries/daily")
def summary_daily(principal: Principal = Depends(require_capability("client.read"))):
    """AI-ready Daily Brief (JSON) — greeting, today's counts, priorities, attention. Read-only."""
    return JSONResponse(summaries.daily_brief(principal))


@router.get("/summaries/opportunities")
def summary_opportunities(principal: Principal = Depends(require_capability("opportunity.read"))):
    """AI-ready Opportunity Summary (JSON) — record-scoped pipeline report."""
    return JSONResponse(summaries.opportunity_summary(principal))


@router.get("/summaries/compliance")
def summary_compliance(principal: Principal = Depends(require_capability("compliance.read"))):
    """AI-ready Compliance Summary (JSON) — record-scoped open-review queue."""
    return JSONResponse(summaries.compliance_summary(principal))


@router.get("/summaries/client/{person_id}")
def summary_client(person_id: int,
                   principal: Principal = Depends(require_capability("client.read"))):
    """AI-ready Client Snapshot (JSON). Enforces person record-scope (404 if out of scope)."""
    snap = summaries.client_snapshot(principal, person_id)
    if snap is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(snap)


@router.get("/summaries/meeting/{person_id}")
def summary_meeting(person_id: int, event: int | None = None,
                    principal: Principal = Depends(require_capability("client.read"))):
    """AI-ready Meeting Prep (JSON). Enforces person record-scope (404 if out of scope)."""
    prep = summaries.meeting_prep(principal, person_id, event_id=event)
    if prep is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(prep)


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
    # Advisor Work (Phase D.9): open-work index for the Create-work / Work-exists action.
    work_index = (advisor_work.open_work_index(principal, person_id)
                  if principal.can("advisor_work.read") else None)
    return templates.TemplateResponse(
        request=request,
        name="workspace/meeting_brief.html",
        context={"principal": principal, "b": brief, "signals": signals, "work_index": work_index},
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
