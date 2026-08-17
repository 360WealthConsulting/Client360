"""Internal Client Portal administration (Phase D.43).

STAFF-facing surface under ``/admin/client-portal/*`` — deliberately NOT under ``/portal`` so it stays on
the internal staff principal + capability RBAC (never the external portal fork). Lets accountable staff
invite portal accounts, revoke access, preview exactly what an account can see (a permissions report built
from the grant scope + visibility registry), and read internal-only diagnostics. There is NO unrestricted
impersonation: staff can preview an account's entitlements but cannot assume its session.
"""
from __future__ import annotations

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
    portal_messages,
    portal_threads,
)
from app.portal import diagnostics as portal_diagnostics
from app.portal import visibility
from app.portal.service import invite_portal_account, portal_scope, staff_send_message
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
                                               "invited": q.get("invited"), "error": q.get("error")})


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
    try:
        account_id, _token = invite_portal_account(
            person_id=person_id, household_id=household_id, email=email.strip(),
            display_name=display_name.strip(), access_type=access_type,
            invited_by_user_id=principal.user_id, organization_id=organization_id)
    except Exception as exc:  # noqa: BLE001 — surface a friendly banner, never a stack trace
        return RedirectResponse(f"/admin/client-portal?error={type(exc).__name__}", status_code=303)
    _audit(request, principal, "portal.admin.invited", account_id,
           {"person_id": person_id, "access_type": access_type, "via": "form"})
    # Token is delivered out-of-band; never shown here.
    return RedirectResponse(f"/admin/client-portal?invited={display_name.strip()}", status_code=303)


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
    scope = portal_scope(account_id)
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


def _thread_in_scope(principal, thread, *, write=False):
    """A thread is in scope when its person OR its household is in the staff record scope."""
    for entity_type, entity_id in (("person", thread["person_id"]), ("household", thread["household_id"])):
        if entity_id is not None and record_in_scope(principal, entity_type, entity_id, write=write):
            return True
    return False


@router.get("/threads", response_class=HTMLResponse)
def portal_admin_threads(request: Request, principal: Principal = Depends(require_capability("client.read"))):
    """Recent secure threads the staff member is allowed to service (record-scoped)."""
    with engine.connect() as connection:
        rows = connection.execute(
            select(portal_threads.c.id, portal_threads.c.subject, portal_threads.c.status,
                   portal_threads.c.updated_at, portal_threads.c.person_id,
                   portal_threads.c.household_id, people.c.full_name)
            .select_from(portal_threads.outerjoin(people, people.c.id == portal_threads.c.person_id))
            .order_by(portal_threads.c.updated_at.desc()).limit(100)).mappings().all()
    threads = [dict(r) for r in rows if _thread_in_scope(principal, r)]
    return templates.TemplateResponse(request=request, name="admin/portal_threads.html",
                                      context={"threads": threads, "principal": principal,
                                               "error": request.query_params.get("error"),
                                               "notice": request.query_params.get("notice")})


@router.get("/threads/{thread_id}", response_class=HTMLResponse)
def portal_admin_thread(thread_id: int, request: Request,
                        principal: Principal = Depends(require_capability("client.read"))):
    thread = _load_thread(thread_id)
    if not thread or not _thread_in_scope(principal, thread):
        raise HTTPException(404, "Thread not found")           # out-of-scope never discloses existence
    with engine.connect() as connection:
        messages = connection.execute(select(portal_messages).where(
            portal_messages.c.thread_id == thread_id).order_by(portal_messages.c.sent_at)).mappings().all()
        client_name = connection.scalar(select(people.c.full_name).where(
            people.c.id == thread["person_id"])) if thread["person_id"] else None
    return templates.TemplateResponse(request=request, name="admin/portal_thread.html", context={
        "thread": dict(thread), "messages": [dict(m) for m in messages], "client_name": client_name,
        "principal": principal, "error": request.query_params.get("error"),
        "notice": request.query_params.get("notice")})


@router.post("/threads/{thread_id}/reply")
def portal_admin_thread_reply(thread_id: int, request: Request, body: str = Form(...),
                              internal_note: str | None = Form(None),
                              principal: Principal = Depends(require_capability("client.write"))):
    """Staff reply (or internal note) into a thread. Requires client.write AND write record scope on
    the thread. Delegates to the existing ``staff_send_message`` service (audited)."""
    thread = _load_thread(thread_id)
    if not thread:
        raise HTTPException(404, "Thread not found")
    if not _thread_in_scope(principal, thread, write=True):
        raise HTTPException(403, "Thread is outside your record scope")
    if not (body or "").strip():
        return RedirectResponse(f"/admin/client-portal/threads/{thread_id}?error=Reply+cannot+be+empty",
                                status_code=303)
    staff_send_message(thread_id=thread_id, user_id=principal.user_id, body=body.strip(),
                       internal_note=bool(internal_note))
    kind = "Internal note added" if internal_note else "Reply sent to client"
    return RedirectResponse(f"/admin/client-portal/threads/{thread_id}?notice={kind.replace(' ', '+')}",
                            status_code=303)


@router.get("/diagnostics")
def portal_admin_diagnostics(principal: Principal = Depends(require_capability("observability.audit"))):
    return portal_diagnostics.portal_diagnostics()
