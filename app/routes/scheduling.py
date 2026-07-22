"""Scheduling routes (Phase D.19) — the enterprise Scheduling & Meeting Management surface.

New ``/scheduling`` prefix. It matches no middleware RULE, so each endpoint enforces its
``scheduling.*`` capability in-route; the service enforces record scope on every read and write.
Sensitive audit history is gated by ``scheduling.audit``; template/resource management by
``scheduling.templates``. Availability and delivery are metadata only (reuse M365 + the
notification ledger). Note: a bare ``/calendar`` prefix is deliberately avoided — that is gated by
``communication.read`` for the Microsoft 365 UI.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.scheduling import availability as avail
from app.services.scheduling import service as svc
from app.services.scheduling import templates as tmpl
from app.templating import install_filters

router = APIRouter(prefix="/scheduling", tags=["scheduling"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


async def _form(request):
    return parse_qs((await request.body()).decode("utf-8"))


def _one(form, key):
    return (form.get(key, [""])[0]).strip()


def _int(form, key):
    v = _one(form, key)
    return int(v) if v else None


def _dt(form, key):
    v = _one(form, key)
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError as exc:
        raise HTTPException(400, f"invalid datetime for {key!r}") from exc


# --- overview + reads --------------------------------------------------------

@router.get("", response_class=HTMLResponse)
def overview(request: Request, status: str | None = None, meeting_type: str | None = None,
             q: str | None = None, upcoming: int = 0, page: int = 1,
             principal: Principal = Depends(require_capability("scheduling.view"))):
    result = svc.list_meetings(principal, status=status, meeting_type=meeting_type, search=q,
                               upcoming_only=bool(upcoming), page=page)
    return templates.TemplateResponse(request=request, name="scheduling/overview.html", context={
        "principal": principal, "result": result, "metrics": svc.metrics(principal),
        "templates": tmpl.list_templates(active_only=True),
        "resources": tmpl.list_resources(active_only=True),
        "filters": {"status": status or "", "meeting_type": meeting_type or "", "q": q or "",
                    "upcoming": bool(upcoming)},
        "can_manage": principal.can("scheduling.manage"),
        "can_templates": principal.can("scheduling.templates")})


@router.get("/templates")
def list_templates(request: Request,
                   principal: Principal = Depends(require_capability("scheduling.view"))):
    return JSONResponse({"templates": [
        {"id": t["id"], "code": t["code"], "name": t["name"], "meeting_type": t["meeting_type"],
         "category": t["category"], "default_duration_minutes": t["default_duration_minutes"],
         "active": t["active"]} for t in tmpl.list_templates()]})


@router.get("/resources")
def list_resources(request: Request,
                   principal: Principal = Depends(require_capability("scheduling.view"))):
    return JSONResponse({"resources": [
        {"id": r["id"], "code": r["code"], "name": r["name"], "resource_type": r["resource_type"],
         "capacity": r["capacity"], "active": r["active"]} for r in tmpl.list_resources()]})


@router.get("/availability")
def availability(request: Request, start: str, end: str, person_id: int | None = None,
                 organizer_user_id: int | None = None,
                 principal: Principal = Depends(require_capability("scheduling.view"))):
    try:
        s, e = datetime.fromisoformat(start), datetime.fromisoformat(end)
    except ValueError as exc:
        raise HTTPException(400, "invalid start/end datetime") from exc
    result = avail.availability(start=s, end=e, person_id=person_id,
                                organizer_user_id=organizer_user_id)
    return JSONResponse({"start": start, "end": end, "free": result["free"],
                         "busy_count": result["busy_count"],
                         "busy": [{"source": b["source"], "subject": b.get("subject"),
                                   "starts_at": b["starts_at"].isoformat() if b.get("starts_at") else None,
                                   "ends_at": b["ends_at"].isoformat() if b.get("ends_at") else None}
                                  for b in result["busy"]]})


@router.get("/{meeting_id}", response_class=HTMLResponse)
def detail(request: Request, meeting_id: int,
           principal: Principal = Depends(require_capability("scheduling.view"))):
    m = svc.get_meeting(principal, meeting_id)
    if m is None:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(request=request, name="scheduling/detail.html", context={
        "principal": principal, "m": m, "can_manage": principal.can("scheduling.manage"),
        "can_audit": principal.can("scheduling.audit")})


@router.get("/{meeting_id}/audit")
def audit(request: Request, meeting_id: int,
          principal: Principal = Depends(require_capability("scheduling.audit"))):
    try:
        history = svc.audit_history(principal, meeting_id)
    except svc.MeetingNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    return JSONResponse({"history": [
        {"event_type": e["event_type"], "from_status": e["from_status"], "to_status": e["to_status"],
         "occurred_at": e["occurred_at"].isoformat()} for e in history]})


# --- meetings ----------------------------------------------------------------

@router.post("")
async def create_meeting(request: Request,
                         principal: Principal = Depends(require_capability("scheduling.manage"))):
    form = await _form(request)
    try:
        m = svc.create_meeting(
            principal, subject=_one(form, "subject"),
            meeting_type=_one(form, "meeting_type") or "general",
            category=_one(form, "category") or "general",
            status=_one(form, "status") or "scheduled",
            priority=_one(form, "priority") or "normal", person_id=_int(form, "person_id"),
            household_id=_int(form, "household_id"), organization_id=_int(form, "organization_id"),
            template_code=_one(form, "template_code") or None, starts_at=_dt(form, "starts_at"),
            ends_at=_dt(form, "ends_at"), location=_one(form, "location") or None,
            location_type=_one(form, "location_type") or "virtual",
            virtual_url=_one(form, "virtual_url") or None,
            opportunity_id=_int(form, "opportunity_id"),
            annual_review_session_id=_int(form, "annual_review_session_id"),
            actor_user_id=principal.user_id)
    except svc.SchedulingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/scheduling/{m['id']}", status_code=303)


@router.post("/{meeting_id}/edit")
async def edit_meeting(request: Request, meeting_id: int,
                       principal: Principal = Depends(require_capability("scheduling.manage"))):
    form = await _form(request)
    fields = {k: _one(form, k) for k in ("subject", "priority", "location", "location_type",
                                         "virtual_url") if _one(form, k)}
    try:
        svc.update_meeting(principal, meeting_id, actor_user_id=principal.user_id, **fields)
    except svc.MeetingNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.SchedulingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/scheduling/{meeting_id}", status_code=303)


def _status_route(status):
    async def handler(request: Request, meeting_id: int,
                      principal: Principal = Depends(require_capability("scheduling.manage"))):
        form = await _form(request)
        try:
            svc.transition(principal, meeting_id, status, actor_user_id=principal.user_id,
                           reason=_one(form, "reason") or None)
        except svc.MeetingNotFound as exc:
            raise HTTPException(404, "Not found") from exc
        except svc.SchedulingError as exc:
            return RedirectResponse(url=f"/scheduling/{meeting_id}?error={exc}", status_code=303)
        return RedirectResponse(url=f"/scheduling/{meeting_id}", status_code=303)
    return handler


router.add_api_route("/{meeting_id}/confirm", _status_route("confirmed"), methods=["POST"])
router.add_api_route("/{meeting_id}/checkin", _status_route("checked_in"), methods=["POST"])
router.add_api_route("/{meeting_id}/cancel", _status_route("cancelled"), methods=["POST"])
router.add_api_route("/{meeting_id}/no-show", _status_route("no_show"), methods=["POST"])


@router.post("/{meeting_id}/reschedule")
async def reschedule(request: Request, meeting_id: int,
                     principal: Principal = Depends(require_capability("scheduling.manage"))):
    form = await _form(request)
    try:
        svc.reschedule(principal, meeting_id, starts_at=_dt(form, "starts_at"),
                       ends_at=_dt(form, "ends_at"), actor_user_id=principal.user_id,
                       reason=_one(form, "reason") or None)
    except svc.MeetingNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.SchedulingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/scheduling/{meeting_id}", status_code=303)


@router.post("/{meeting_id}/outcome")
async def outcome(request: Request, meeting_id: int,
                  principal: Principal = Depends(require_capability("scheduling.manage"))):
    form = await _form(request)
    try:
        svc.record_outcome(principal, meeting_id, outcome=_one(form, "outcome") or None,
                           outcome_notes=_one(form, "outcome_notes") or None,
                           complete=(_one(form, "complete") != "0"), actor_user_id=principal.user_id)
    except svc.MeetingNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.SchedulingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/scheduling/{meeting_id}", status_code=303)


# --- attendees / bookings / reminders / follow-ups ---------------------------

@router.post("/{meeting_id}/attendees")
async def add_attendee(request: Request, meeting_id: int,
                       principal: Principal = Depends(require_capability("scheduling.manage"))):
    form = await _form(request)
    try:
        svc.add_attendee(principal, meeting_id, attendee_ref=_one(form, "attendee_ref"),
                         attendee_type=_one(form, "attendee_type") or "person",
                         attendee_role=_one(form, "attendee_role") or "required",
                         display_name=_one(form, "display_name") or None,
                         actor_user_id=principal.user_id)
    except svc.MeetingNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.SchedulingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/scheduling/{meeting_id}", status_code=303)


@router.post("/{meeting_id}/resources")
async def book_resource(request: Request, meeting_id: int,
                        principal: Principal = Depends(require_capability("scheduling.manage"))):
    form = await _form(request)
    try:
        svc.book_resource(principal, meeting_id, _int(form, "resource_id"),
                          actor_user_id=principal.user_id)
    except svc.MeetingNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.SchedulingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/scheduling/{meeting_id}", status_code=303)


@router.post("/{meeting_id}/reminders")
async def add_reminder(request: Request, meeting_id: int,
                       principal: Principal = Depends(require_capability("scheduling.manage"))):
    form = await _form(request)
    try:
        svc.add_reminder(principal, meeting_id, minutes_before=_int(form, "minutes_before"),
                         channel=_one(form, "channel") or "internal_notification",
                         actor_user_id=principal.user_id)
    except svc.MeetingNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.SchedulingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/scheduling/{meeting_id}", status_code=303)


@router.post("/{meeting_id}/followups")
async def add_followup(request: Request, meeting_id: int,
                       principal: Principal = Depends(require_capability("scheduling.manage"))):
    form = await _form(request)
    try:
        svc.add_followup(principal, meeting_id, description=_one(form, "description"),
                         due_date=_dt(form, "due_date"), assigned_user_id=_int(form, "assigned_user_id"),
                         actor_user_id=principal.user_id)
    except svc.MeetingNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.SchedulingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/scheduling/{meeting_id}", status_code=303)


# --- templates + resources ---------------------------------------------------

@router.post("/templates")
async def create_template(request: Request,
                          principal: Principal = Depends(require_capability("scheduling.templates"))):
    form = await _form(request)
    try:
        tmpl.create_template(code=_one(form, "code"), name=_one(form, "name"),
                             meeting_type=_one(form, "meeting_type") or "general",
                             category=_one(form, "category") or "general",
                             default_duration_minutes=_int(form, "default_duration_minutes") or 60,
                             default_location_type=_one(form, "default_location_type") or "virtual",
                             actor_user_id=principal.user_id)
    except tmpl.TemplateError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/scheduling", status_code=303)


@router.post("/resources")
async def create_resource(request: Request,
                          principal: Principal = Depends(require_capability("scheduling.templates"))):
    form = await _form(request)
    try:
        tmpl.create_resource(code=_one(form, "code"), name=_one(form, "name"),
                             resource_type=_one(form, "resource_type") or "room",
                             capacity=_int(form, "capacity"), location=_one(form, "location") or None)
    except tmpl.TemplateError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/scheduling", status_code=303)
