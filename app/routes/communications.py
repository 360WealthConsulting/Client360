"""Communications routes (Phase D.18) — the enterprise Communications & Client Engagement surface.

New ``/communications`` prefix. It matches no middleware RULE, so each endpoint enforces its
``communications.*`` capability in-route; the service enforces record scope on every read and
write. Sensitive audit history is gated by ``communications.audit``; template management by
``communications.manage_templates``. Delivery is metadata only (reuses the notification ledger).
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.communications import delivery
from app.services.communications import service as svc
from app.services.communications import templates as tmpl
from app.templating import install_filters

router = APIRouter(prefix="/communications", tags=["communications"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


async def _form(request):
    return parse_qs((await request.body()).decode("utf-8"))


def _one(form, key):
    return (form.get(key, [""])[0]).strip()


def _int(form, key):
    v = _one(form, key)
    return int(v) if v else None


def _recipients_from_form(form):
    """Parse repeated recipient_ref[]/recipient_type[]/recipient_role[] fields into a list."""
    refs = form.get("recipient_ref", []) or form.get("recipient_ref[]", [])
    types = form.get("recipient_type", []) or form.get("recipient_type[]", [])
    roles = form.get("recipient_role", []) or form.get("recipient_role[]", [])
    out = []
    for i, ref in enumerate(refs):
        if not ref.strip():
            continue
        out.append({"recipient_ref": ref.strip(),
                    "recipient_type": (types[i] if i < len(types) else "person") or "person",
                    "recipient_role": (roles[i] if i < len(roles) else "to") or "to"})
    return out


# --- overview + reads --------------------------------------------------------

@router.get("", response_class=HTMLResponse)
def overview(request: Request, status: str | None = None, category: str | None = None,
             q: str | None = None, page: int = 1,
             principal: Principal = Depends(require_capability("communications.view"))):
    result = svc.list_conversations(principal, status=status, category=category, search=q, page=page)
    return templates.TemplateResponse(request=request, name="communications/overview.html", context={
        "principal": principal, "result": result, "metrics": svc.metrics(principal),
        "templates": tmpl.list_templates(active_only=True),
        "filters": {"status": status or "", "category": category or "", "q": q or ""},
        "can_send": principal.can("communications.send"),
        "can_manage_templates": principal.can("communications.manage_templates")})


@router.get("/templates")
def list_templates(request: Request,
                   principal: Principal = Depends(require_capability("communications.view"))):
    return JSONResponse({"templates": [
        {"id": t["id"], "code": t["code"], "name": t["name"], "category": t["category"],
         "channel": t["channel"], "active": t["active"]} for t in tmpl.list_templates()]})


@router.get("/{conversation_id}", response_class=HTMLResponse)
def detail(request: Request, conversation_id: int,
           principal: Principal = Depends(require_capability("communications.view"))):
    conv = svc.get_conversation(principal, conversation_id)
    if conv is None:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(request=request, name="communications/detail.html", context={
        "principal": principal, "c": conv, "channels": svc.COMMUNICATION_CHANNELS,
        "can_send": principal.can("communications.send"),
        "can_audit": principal.can("communications.audit")})


@router.get("/{conversation_id}/audit")
def audit(request: Request, conversation_id: int,
          principal: Principal = Depends(require_capability("communications.audit"))):
    try:
        history = svc.audit_history(principal, conversation_id)
    except svc.CommunicationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    return JSONResponse({"history": [
        {"event_type": e["event_type"], "message_id": e["message_id"],
         "occurred_at": e["occurred_at"].isoformat()} for e in history]})


@router.get("/messages/{message_id}/deliveries")
def message_deliveries(request: Request, message_id: int,
                       principal: Principal = Depends(require_capability("communications.view"))):
    try:
        rows = svc.message_delivery_history(principal, message_id)
    except svc.CommunicationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    return JSONResponse({"deliveries": [
        {"status": r["status"], "channel": r["channel"], "provider": r["provider"],
         "occurred_at": r["occurred_at"].isoformat()} for r in rows]})


# --- conversations -----------------------------------------------------------

@router.post("")
async def create_conversation(request: Request,
                              principal: Principal = Depends(require_capability("communications.send"))):
    form = await _form(request)
    try:
        conv = svc.create_conversation(
            principal, subject=_one(form, "subject"),
            category=_one(form, "category") or "general",
            priority=_one(form, "priority") or "normal",
            channel=_one(form, "channel") or "email", person_id=_int(form, "person_id"),
            household_id=_int(form, "household_id"), organization_id=_int(form, "organization_id"),
            actor_user_id=principal.user_id)
    except svc.CommunicationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/communications/{conv['id']}", status_code=303)


@router.post("/{conversation_id}/threads")
async def create_thread(request: Request, conversation_id: int,
                        principal: Principal = Depends(require_capability("communications.send"))):
    form = await _form(request)
    try:
        svc.create_thread(principal, conversation_id, subject=_one(form, "subject") or None,
                          actor_user_id=principal.user_id)
    except svc.CommunicationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.CommunicationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/communications/{conversation_id}", status_code=303)


def _status_route(status, cap):
    async def handler(request: Request, conversation_id: int,
                      principal: Principal = Depends(require_capability(cap))):
        try:
            svc.set_status(principal, conversation_id, status, actor_user_id=principal.user_id)
        except svc.CommunicationNotFound as exc:
            raise HTTPException(404, "Not found") from exc
        except svc.CommunicationError as exc:
            raise HTTPException(400, str(exc)) from exc
        return RedirectResponse(url=f"/communications/{conversation_id}", status_code=303)
    return handler


router.add_api_route("/{conversation_id}/close", _status_route("closed", "communications.send"),
                     methods=["POST"])
router.add_api_route("/{conversation_id}/archive", _status_route("archived", "communications.send"),
                     methods=["POST"])
router.add_api_route("/{conversation_id}/reopen", _status_route("open", "communications.send"),
                     methods=["POST"])


# --- messages + delivery -----------------------------------------------------

@router.post("/{conversation_id}/messages")
async def send_message(request: Request, conversation_id: int,
                       principal: Principal = Depends(require_capability("communications.send"))):
    form = await _form(request)
    try:
        svc.send_message(
            principal, conversation_id, body=_one(form, "body") or None,
            subject=_one(form, "subject") or None, channel=_one(form, "channel") or None,
            direction=_one(form, "direction") or "outbound",
            priority=_one(form, "priority") or None, category=_one(form, "category") or None,
            thread_id=_int(form, "thread_id"), template_code=_one(form, "template_code") or None,
            recipients_in=_recipients_from_form(form),
            mark_sent=(_one(form, "mark_sent") != "0"), actor_user_id=principal.user_id)
    except svc.CommunicationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.CommunicationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/communications/{conversation_id}", status_code=303)


@router.post("/messages/{message_id}/deliver")
async def deliver(request: Request, message_id: int,
                  principal: Principal = Depends(require_capability("communications.send"))):
    form = await _form(request)
    status = _one(form, "status") or "delivered"
    try:
        msg = svc.transition_delivery(principal, message_id, status,
                                      provider=_one(form, "provider") or None,
                                      detail=_one(form, "detail") or None,
                                      actor_user_id=principal.user_id)
    except svc.CommunicationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except (svc.CommunicationError, delivery.DeliveryError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/communications/{msg['conversation_id']}", status_code=303)


@router.post("/messages/{message_id}/read")
async def mark_read(request: Request, message_id: int,
                    principal: Principal = Depends(require_capability("communications.view"))):
    try:
        msg = svc.mark_read(principal, message_id, actor_user_id=principal.user_id)
    except svc.CommunicationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except (svc.CommunicationError, delivery.DeliveryError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/communications/{msg['conversation_id']}", status_code=303)


@router.post("/messages/{message_id}/cancel")
async def cancel(request: Request, message_id: int,
                 principal: Principal = Depends(require_capability("communications.send"))):
    try:
        msg = svc.cancel_message(principal, message_id, actor_user_id=principal.user_id)
    except svc.CommunicationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except (svc.CommunicationError, delivery.DeliveryError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/communications/{msg['conversation_id']}", status_code=303)


# --- templates ---------------------------------------------------------------

@router.post("/templates")
async def create_template(request: Request,
                          principal: Principal = Depends(require_capability("communications.manage_templates"))):
    form = await _form(request)
    try:
        tmpl.create_template(code=_one(form, "code"), name=_one(form, "name"),
                             body=_one(form, "body"), category=_one(form, "category") or "general",
                             channel=_one(form, "channel") or "email",
                             subject=_one(form, "subject") or None, actor_user_id=principal.user_id)
    except tmpl.TemplateError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/communications", status_code=303)


@router.post("/templates/{template_id}")
async def update_template(request: Request, template_id: int,
                          principal: Principal = Depends(require_capability("communications.manage_templates"))):
    form = await _form(request)
    active = _one(form, "active")
    try:
        tmpl.update_template(template_id, name=_one(form, "name") or None,
                             body=_one(form, "body") or None, subject=_one(form, "subject") or None,
                             category=_one(form, "category") or None,
                             channel=_one(form, "channel") or None,
                             active=(active == "1") if active in ("0", "1") else None)
    except tmpl.TemplateError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/communications", status_code=303)
