"""Internal Client Portal administration (Phase D.43).

STAFF-facing surface under ``/admin/client-portal/*`` — deliberately NOT under ``/portal`` so it stays on
the internal staff principal + capability RBAC (never the external portal fork). Lets accountable staff
invite portal accounts, revoke access, preview exactly what an account can see (a permissions report built
from the grant scope + visibility registry), and read internal-only diagnostics. There is NO unrestricted
impersonation: staff can preview an account's entitlements but cannot assume its session.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select

from app.db import (
    engine,
    people,
    portal_access_grants,
    portal_accounts,
    portal_threads,
)
from app.portal import communication_hub as hub
from app.portal import diagnostics as portal_diagnostics
from app.portal import invitation_handoff
from app.portal import visibility
from app.portal.service import invite_portal_account, portal_base_scope, staff_send_message
from app.security.audit import write_audit_event
from app.security.authorization import record_in_scope
from app.security.dependencies import require_capability
from app.security.models import Principal

router = APIRouter(prefix="/admin/client-portal", tags=["client-portal-admin"])
templates = Jinja2Templates(directory="app/templates")


class PortalInvite(BaseModel):
    person_id: int
    household_id: int
    email: str
    display_name: str
    access_type: str = "self"
    organization_id: int | None = None


def _audit(request, principal, action, entity_id=None, metadata=None):
    write_audit_event(action=action, entity_type="portal_account", entity_id=entity_id,
                      actor_user_id=principal.user_id, request_id=request.state.request_id,
                      ip_address=request.client.host if request.client else None,
                      user_agent=request.headers.get("user-agent"), metadata=metadata)


#: Session key holding the OPAQUE handle to a pending activation link. Never the link itself: the
#: session cookie is signed, not encrypted.
HANDOFF_SESSION_KEY = "portal_invitation_handoff"


def _activation_url(request, raw_token: str) -> str:
    """The client's activation URL, on the CANONICAL external origin.

    Built from the application's own route name through ``external_url`` — never the inbound Host
    header. An invitation link derived from an attacker-supplied Host would hand the client's
    single-use token to whatever origin that header named. The token is URL-encoded so it survives
    the query string intact."""
    from app.security.origin import external_url
    return f"{external_url(request, 'portal_login')}?invitation={quote(raw_token, safe='')}"


def _remember_activation_url(request, *, display_name: str, url: str) -> None:
    """Hold the link server-side for ONE read by this staff member's next page load."""
    session = getattr(request, "session", None)
    if session is None:                       # no session middleware (direct service call) — skip
        return
    session[HANDOFF_SESSION_KEY] = invitation_handoff.stash(
        {"display_name": display_name, "url": url})


def _take_activation_url(request):
    """Pop the one-time activation link, if this staff member has just created one.

    Both the handle and the stored payload are removed, so a refresh or any later page load renders
    nothing at all."""
    session = getattr(request, "session", None)
    if session is None:
        return None
    return invitation_handoff.take(session.pop(HANDOFF_SESSION_KEY, None))


def _accounts():
    with engine.connect() as connection:
        return [dict(r) for r in connection.execute(select(
            portal_accounts.c.id, portal_accounts.c.display_name, portal_accounts.c.email,
            portal_accounts.c.status, portal_accounts.c.mfa_enabled, portal_accounts.c.last_login_at,
            portal_accounts.c.person_id).order_by(portal_accounts.c.created_at.desc())).mappings().all()]


@router.get("", response_class=HTMLResponse)
def portal_admin_home(request: Request, principal: Principal = Depends(require_capability("client.read"))):
    q = request.query_params
    return templates.TemplateResponse(request=request, name="admin/client_portal.html",
                                      context={"accounts": _accounts(), "principal": principal,
                                               "invited": q.get("invited"), "error": q.get("error"),
                                               # Read once; gone on the next load.
                                               "activation": _take_activation_url(request)})


@router.get("/accounts")
def portal_admin_accounts(principal: Principal = Depends(require_capability("client.read"))):
    return {"accounts": _accounts()}


@router.post("/invite", status_code=201)
def portal_admin_invite(payload: PortalInvite, request: Request,
                        principal: Principal = Depends(require_capability("client.write"))):
    # Record-level scope: staff may only invite for a person they can service.
    if not record_in_scope(principal, "person", payload.person_id, write=True):
        raise HTTPException(403, "Person is outside your record scope")
    account_id, _token = invite_portal_account(
        person_id=payload.person_id, household_id=payload.household_id, email=payload.email,
        display_name=payload.display_name, access_type=payload.access_type,
        invited_by_user_id=principal.user_id, organization_id=payload.organization_id)
    _audit(request, principal, "portal.admin.invited", account_id,
           {"person_id": payload.person_id, "access_type": payload.access_type})
    # The activation token is NEVER returned in the response or logged — delivery is out-of-band.
    return {"account_id": account_id, "status": "invited"}


@router.post("/invite-form")
def portal_admin_invite_form(
        request: Request,
        person_id: int = Form(...), household_id: int = Form(...), email: str = Form(...),
        display_name: str = Form(...), access_type: str = Form("self"),
        organization_id: int | None = Form(None),
        principal: Principal = Depends(require_capability("client.write"))):
    """Browser form over the SAME scoped/audited invite service as POST /invite. Post/Redirect/Get so a
    refresh never re-invites; a record-scope failure redirects with an error banner instead of a raw 403."""
    if not record_in_scope(principal, "person", person_id, write=True):
        return RedirectResponse("/admin/client-portal?error=Person+is+outside+your+record+scope",
                                status_code=303)
    name = display_name.strip()
    try:
        account_id, raw_token = invite_portal_account(
            person_id=person_id, household_id=household_id, email=email.strip(),
            display_name=name, access_type=access_type,
            invited_by_user_id=principal.user_id, organization_id=organization_id)
    except Exception as exc:  # noqa: BLE001 — surface a friendly banner, never a stack trace
        return RedirectResponse(f"/admin/client-portal?error={type(exc).__name__}", status_code=303)
    # Metadata deliberately carries person/access only — NEVER the token or the activation URL.
    _audit(request, principal, "portal.admin.invited", account_id,
           {"person_id": person_id, "access_type": access_type, "via": "form"})
    # The invitation is undeliverable without the raw token, and there is no production-capable email
    # channel to send it (see app/portal/invitation_handoff.py). Hand the link to the staff member
    # who created it, exactly once, through a server-side store. It never enters this redirect URL.
    try:
        _remember_activation_url(request, display_name=name,
                                 url=_activation_url(request, raw_token))
    except Exception:  # noqa: BLE001 — the account IS invited; only the convenience link is lost
        return RedirectResponse(f"/admin/client-portal?invited={quote(name)}"
                                "&error=Activation+link+unavailable%3A+check+PUBLIC_BASE_URL",
                                status_code=303)
    return RedirectResponse(f"/admin/client-portal?invited={quote(name)}", status_code=303)


@router.post("/accounts/{account_id}/revoke", status_code=200)
def portal_admin_revoke(account_id: int, request: Request,
                        principal: Principal = Depends(require_capability("client.write"))):
    from datetime import date
    with engine.begin() as connection:
        acct = connection.execute(select(portal_accounts.c.person_id).where(
            portal_accounts.c.id == account_id)).mappings().one_or_none()
        if not acct:
            raise HTTPException(404, "Portal account not found")
        if not record_in_scope(principal, "person", acct["person_id"], write=True):
            raise HTTPException(403, "Person is outside your record scope")
        connection.execute(portal_accounts.update().where(portal_accounts.c.id == account_id).values(
            status="revoked"))
        connection.execute(portal_access_grants.update().where(
            portal_access_grants.c.portal_account_id == account_id,
            portal_access_grants.c.inactive_date.is_(None)).values(inactive_date=date.today()))
    _audit(request, principal, "portal.admin.revoked", account_id)
    return {"account_id": account_id, "status": "revoked"}


@router.get("/accounts/{account_id}/preview")
def portal_admin_preview(account_id: int, principal: Principal = Depends(require_capability("client.read"))):
    """A permissions report: exactly what this account can see, derived from its grant scope + the
    visibility registry. This is NOT impersonation — no session is created, only entitlements shown."""
    with engine.connect() as connection:
        acct = connection.execute(select(portal_accounts.c.person_id).where(
            portal_accounts.c.id == account_id)).mappings().one_or_none()
    if not acct:
        raise HTTPException(404, "Portal account not found")
    if not record_in_scope(principal, "person", acct["person_id"]):
        raise HTTPException(403, "Person is outside your record scope")
    # Staff entitlement preview must report the WHOLE grant scope, not one permission's slice.
    scope = portal_base_scope(account_id)
    granted = set()
    for g in scope["grants"]:
        for perm, on in (g["permissions"] or {}).items():
            if on:
                granted.add(perm)
    fields = []
    for f in visibility.external_fields():
        entitled = f.required_permission is None or f.required_permission in granted
        fields.append({"key": f.key, "source": f.source_service, "requires": f.required_permission,
                       "scope": f.required_scope, "masking": f.masking_rule, "entitled": entitled})
    return {"account_id": account_id, "granted_permissions": sorted(granted),
            "household_ids": sorted(scope["household_ids"]), "person_count": len(scope["person_ids"]),
            "visible_fields": fields}


# --- Staff secure-messaging: the reply side of the two-way conversation --------
# A portal client can open/read/reply to a thread on their own record (external portal
# principal). This is the STAFF counterpart: an accountable employee reads the thread
# (including internal notes) and replies through the existing ``staff_send_message`` service.
# Authorization is BOTH capability (client.read to view, client.write to reply) AND record
# scope on the thread's person/household — the buttons never carry authority; every handler
# re-checks. Out-of-scope threads deny existence with 404.

def _load_thread(thread_id):
    with engine.connect() as connection:
        return connection.execute(select(portal_threads).where(
            portal_threads.c.id == thread_id)).mappings().one_or_none()


@router.get("/threads", response_class=HTMLResponse)
def portal_admin_threads(request: Request, principal: Principal = Depends(require_capability("client.read"))):
    """Communication hub work-queue: conversations the staff member can service, filterable by unread /
    assigned-to-me / unassigned / topic / status (record-scoped)."""
    q = request.query_params
    threads = hub.staff_inbox(
        principal, unread=q.get("filter") == "unread",
        assigned_to_me=q.get("filter") == "mine", unassigned=q.get("filter") == "unassigned",
        topic=q.get("topic") or None, status=q.get("status") or None)
    return templates.TemplateResponse(request=request, name="admin/portal_threads.html",
                                      context={"threads": threads, "principal": principal,
                                               "topics": hub.TOPICS, "active_filter": q.get("filter"),
                                               "active_topic": q.get("topic"), "active_status": q.get("status"),
                                               "error": q.get("error"), "notice": q.get("notice")})


@router.get("/threads/{thread_id}", response_class=HTMLResponse)
def portal_admin_thread(thread_id: int, request: Request,
                        principal: Principal = Depends(require_capability("client.read"))):
    thread = _load_thread(thread_id)
    if not thread or not hub.thread_in_staff_scope(principal, thread):
        raise HTTPException(404, "Thread not found")           # out-of-scope never discloses existence
    messages = hub.staff_thread_messages(thread_id)            # includes internal notes (staff view)
    client_name = None
    if thread["person_id"]:
        with engine.connect() as connection:
            client_name = connection.scalar(select(people.c.full_name).where(
                people.c.id == thread["person_id"]))
    hub.mark_thread_read_staff(thread_id, actor_user_id=principal.user_id)   # relationship-level read
    return templates.TemplateResponse(request=request, name="admin/portal_thread.html", context={
        "thread": dict(thread), "messages": messages, "client_name": client_name,
        "assigned_name": hub.staff_name(thread["assigned_user_id"]),
        "linked_requests": hub.linked_requests(thread_id), "topics": hub.TOPICS,
        "assignable_users": hub.assignable_users(), "assignable_teams": hub.assignable_teams(),
        "principal": principal, "error": request.query_params.get("error"),
        "notice": request.query_params.get("notice")})


@router.post("/threads/{thread_id}/reply")
def portal_admin_thread_reply(thread_id: int, request: Request, body: str = Form(...),
                              internal_note: str | None = Form(None),
                              principal: Principal = Depends(require_capability("client.write"))):
    """Staff reply (or internal note) into a thread. Requires client.write AND write record scope on
    the thread. Delegates to the existing ``staff_send_message`` service (audited)."""
    _guard_thread_write(principal, thread_id)
    if not (body or "").strip():
        return RedirectResponse(f"/admin/client-portal/threads/{thread_id}?error=Reply+cannot+be+empty",
                                status_code=303)
    staff_send_message(thread_id=thread_id, user_id=principal.user_id, body=body.strip(),
                       internal_note=bool(internal_note))
    kind = "Internal note added" if internal_note else "Reply sent to client"
    return RedirectResponse(f"/admin/client-portal/threads/{thread_id}?notice={kind.replace(' ', '+')}",
                            status_code=303)


def _guard_thread_write(principal, thread_id):
    """Load a thread + enforce write record scope. Returns the thread or raises 404/403."""
    thread = _load_thread(thread_id)
    if not thread:
        raise HTTPException(404, "Thread not found")
    if not hub.thread_in_staff_scope(principal, thread, write=True):
        raise HTTPException(403, "Thread is outside your record scope")
    return thread


def _thread_redirect(thread_id, *, notice=None, error=None):
    q = ("?notice=" + quote(notice)) if notice else (("?error=" + quote(error)) if error else "")
    return RedirectResponse(f"/admin/client-portal/threads/{thread_id}{q}", status_code=303)


def _opt_int(value):
    """Coerce a selector value ('' = Unassigned) to int or None; a non-numeric value is invalid.
    Accepts an int directly (direct service/test calls) as well as the form's string."""
    if value is None or isinstance(value, int):
        return value
    v = value.strip()
    if not v:
        return None
    if not v.isdigit():
        raise ValueError("Invalid selection")
    return int(v)


@router.post("/threads/{thread_id}/assign")
def portal_admin_thread_assign(thread_id: int, request: Request,
                               assigned_user_id: str | None = Form(None),
                               assigned_team_id: str | None = Form(None), topic: str | None = Form(None),
                               principal: Principal = Depends(require_capability("client.write"))):
    """Reassign / route a conversation and/or set its topic from the employee/team selectors (audited
    prev→new). client.write + record scope; only valid, selectable users/teams are accepted server-side;
    an empty selection is the valid Unassigned state."""
    _guard_thread_write(principal, thread_id)
    try:
        user_id, team_id = _opt_int(assigned_user_id), _opt_int(assigned_team_id)
        hub.reassign_thread(principal.user_id, thread_id, user_id=user_id, team_id=team_id,
                            topic=topic or None, request_id=request.state.request_id)
    except ValueError as exc:
        return _thread_redirect(thread_id, error=str(exc))
    return _thread_redirect(thread_id, notice="Conversation routing updated.")


@router.post("/threads/{thread_id}/resolve")
def portal_admin_thread_resolve(thread_id: int, request: Request, action: str = Form("resolve"),
                                principal: Principal = Depends(require_capability("client.write"))):
    """Resolve or reopen a conversation (audited). client.write + record scope."""
    _guard_thread_write(principal, thread_id)
    hub.set_thread_state(principal.user_id, thread_id, resolved=(action == "resolve"),
                         request_id=request.state.request_id)
    return _thread_redirect(thread_id, notice=f"Conversation {action}d.")


@router.post("/threads/{thread_id}/link-request")
def portal_admin_thread_link_request(thread_id: int, request: Request, request_ref: int = Form(...),
                                     principal: Principal = Depends(require_capability("client.write"))):
    """Link an existing document request to this conversation (same client only). client.write + scope."""
    _guard_thread_write(principal, thread_id)
    try:
        hub.link_request(principal.user_id, thread_id, request_ref, request_id=request.state.request_id)
    except PermissionError:
        return _thread_redirect(thread_id, error="That request belongs to a different client.")
    except ValueError as exc:
        return _thread_redirect(thread_id, error=str(exc))
    return _thread_redirect(thread_id, notice="Request linked to conversation.")


@router.post("/threads/{thread_id}/create-request")
def portal_admin_thread_create_request(thread_id: int, request: Request, title: str = Form(...),
                                       description: str | None = Form(None),
                                       principal: Principal = Depends(require_capability("client.write"))):
    """Turn a conversation into an actionable document request (linked back). client.write + scope."""
    _guard_thread_write(principal, thread_id)
    if not (title or "").strip():
        return _thread_redirect(thread_id, error="A request title is required.")
    hub.create_request_from_thread(principal.user_id, thread_id, title=title.strip(),
                                   description=(description or None), request_id=request.state.request_id)
    return _thread_redirect(thread_id, notice="Document request created and linked.")


@router.get("/diagnostics")
def portal_admin_diagnostics(principal: Principal = Depends(require_capability("observability.audit"))):
    return portal_diagnostics.portal_diagnostics()
