"""Annual Review Workspace routes (Phase D.11).

One advisor-facing workspace that composes existing services (Client360, Advisor
Intelligence, Advisor Work, Activity Timeline, Compliance, Meeting Workspace,
Portfolio) into a read-first annual-review screen, plus a mutable review *session*
(notes + presentation-only checklist) that records advisor activity only.

Gated server-side by ``annual_review.read/create/update``. ``/annual-review/*`` is
outside the ``^/(people|households)`` middleware RECORD_PATH, so the service enforces
person record scope itself (scope-first). ``annual_review.*`` is never a bypass around
``advisor_work.read`` / ``timeline.read`` / ``compliance.review.read`` — the composed
sections are gated per owning capability in the service. No workflow engine, no bulk
actions, no source-domain mutation.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import annual_review as svc
from app.templating import install_filters

router = APIRouter(prefix="/annual-review", tags=["annual-review"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)  # humandt for timestamps


async def _form(request: Request) -> dict[str, list[str]]:
    return parse_qs((await request.body()).decode("utf-8"))


def _context(request, principal, workspace, *, session=None, completed=()):
    return {
        "request": request, "principal": principal, "ws": workspace,
        "session": session, "completed_sessions": completed,
        "checklist_items": svc.CHECKLIST_ITEMS,
        "can_create": principal.can("annual_review.create"),
        "can_update": principal.can("annual_review.update"),
        "editable": bool(session) and session["status"] in svc.EDITABLE_STATUSES,
    }


@router.get("/{person_id}", response_class=HTMLResponse)
def workspace(
    request: Request, person_id: int,
    principal: Principal = Depends(require_capability("annual_review.read")),
):
    session = svc.open_session_for(principal, person_id)
    ws = svc.compose_workspace(principal, person_id, session=session)
    if ws is None:
        raise HTTPException(404, "Not found")
    completed = svc.list_completed_sessions(principal, person_id)
    return templates.TemplateResponse(
        request=request, name="annual_review/workspace.html",
        context=_context(request, principal, ws, session=session, completed=completed))


@router.post("/{person_id}/start")
async def start(
    request: Request, person_id: int,
    principal: Principal = Depends(require_capability("annual_review.create")),
):
    try:
        session = svc.start_session(principal, person_id, advisor_id=principal.user_id)
    except svc.SessionNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return RedirectResponse(url=f"/annual-review/session/{session['id']}", status_code=303)


@router.get("/session/{session_id}", response_class=HTMLResponse)
def session_view(
    request: Request, session_id: int,
    principal: Principal = Depends(require_capability("annual_review.read")),
):
    session = svc.get_session(principal, session_id)
    if session is None:
        raise HTTPException(404, "Not found")
    ws = svc.compose_workspace(principal, session["person_id"], session=session)
    if ws is None:
        raise HTTPException(404, "Not found")
    completed = svc.list_completed_sessions(principal, session["person_id"])
    return templates.TemplateResponse(
        request=request, name="annual_review/workspace.html",
        context=_context(request, principal, ws, session=session, completed=completed))


@router.post("/session/{session_id}")
async def update_session(
    request: Request, session_id: int,
    principal: Principal = Depends(require_capability("annual_review.update")),
):
    form = await _form(request)
    action = (form.get("action", [""])[0]).strip()
    try:
        if action == "complete":
            svc.set_status(principal, session_id, new_status="completed")
        elif action == "archive":
            svc.set_status(principal, session_id, new_status="archived")
        else:  # save notes + checklist
            checked = {k: True for k in form.get("checklist", [])}
            notes = form.get("notes", [""])[0]
            svc.save_session(principal, session_id, notes=notes, checklist_state=checked)
    except svc.SessionNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except svc.InvalidSessionTransitionError as exc:
        return RedirectResponse(url=f"/annual-review/session/{session_id}?error={exc}",
                                status_code=303)
    return RedirectResponse(url=f"/annual-review/session/{session_id}", status_code=303)
