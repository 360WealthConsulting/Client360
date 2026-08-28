"""Internal Client Portal administration (Phase D.43).

STAFF-facing surface under ``/admin/client-portal/*`` — deliberately NOT under ``/portal`` so it stays on
the internal staff principal + capability RBAC (never the external portal fork). Lets accountable staff
invite portal accounts, revoke access, preview exactly what an account can see (a permissions report built
from the grant scope + visibility registry), and read internal-only diagnostics. There is NO unrestricted
impersonation: staff can preview an account's entitlements but cannot assume its session.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.db import (
    engine,
    people,
    portal_accounts,
    portal_invitations,
    portal_threads,
)
from app.portal import communication_hub as hub
from app.portal import diagnostics as portal_diagnostics
from app.portal import invitation_handoff
from app.portal import invite_targets
from app.services import email_delivery, person_creation
from app.portal import visibility
from app.portal.service import (PortalAccountConflictError, invite_portal_account,
                                portal_base_scope, staff_send_message)
from app.security.audit import write_audit_event
from app.security.authorization import record_in_scope
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.templating import wants_html

router = APIRouter(prefix="/admin/client-portal", tags=["client-portal-admin"])

logger = logging.getLogger("client360.portal.admin")

#: Shown when the invitation fails for a reason staff cannot act on individually. Deliberately fixed
#: and non-technical — a database exception class name is not an instruction to anybody.
INVITE_FAILED_ERROR = ("The invitation could not be created. Please try again, and contact support "
                       "if it keeps happening.")
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

#: Shown when the form is submitted with no client chosen. Identical wording to the client-side
#: guard, so the message does not change depending on whether JavaScript ran.
NO_SELECTION_ERROR = "Select a client before sending the invitation."
DEFAULT_ACCESS = invite_targets.DEFAULT_ACCESS_TYPE


def _activation_url(request, raw_token: str) -> str:
    """The client's activation URL, on the CANONICAL external origin.

    Built from the application's own route name through ``external_url`` — never the inbound Host
    header. An invitation link derived from an attacker-supplied Host would hand the client's
    single-use token to whatever origin that header named. The token is URL-encoded so it survives
    the query string intact."""
    from app.security.origin import external_url
    # Points at the ACTIVATION route, not the login page: that route moves the token straight into
    # the session and redirects to a clean URL, so the credential leaves the address bar after one
    # hop instead of sitting in history and the referrer header.
    return f"{external_url(request, 'portal_activate')}?invitation={quote(raw_token, safe='')}"


def _remember_activation_url(request, *, display_name: str, url: str, delivery=None) -> None:
    """Hold the link — and how delivery went — server-side for ONE read by the next page load.

    The delivery outcome rides along here rather than in the redirect URL so a client's email
    address never enters browser history, proxy logs or the referrer header."""
    session = getattr(request, "session", None)
    if session is None:                       # no session middleware (direct service call) — skip
        return
    session[HANDOFF_SESSION_KEY] = invitation_handoff.stash(
        {"display_name": display_name, "url": url,
         "delivered": bool(delivery and delivery.delivered),
         # Staff-facing sentence only. Never a Graph body, never the activation URL.
         "delivery_detail": (delivery.detail if delivery else ""),
         "delivery_status": (delivery.status if delivery else "")})


def _take_activation_url(request):
    """Pop the one-time activation link, if this staff member has just created one.

    Both the handle and the stored payload are removed, so a refresh or any later page load renders
    nothing at all."""
    session = getattr(request, "session", None)
    if session is None:
        return None
    return invitation_handoff.take(session.pop(HANDOFF_SESSION_KEY, None))


def _invitation_state(row, now) -> str:
    """The LATEST invitation for an account, as one human-readable phrase.

    Derived entirely from canonical ``portal_invitations`` columns — never guessed, never computed in
    the browser. Carries no invitation id, token or hash: only what a staff member needs to know
    whether the link they sent still works."""
    if row["invited_at"] is None:
        return "No invitation"
    if row["revoked_at"] is not None:
        return "Revoked"
    if row["accepted_at"] is not None:
        return "Accepted"
    expires_at = row["expires_at"]
    if expires_at is None:
        return "Pending"
    if expires_at <= now:
        return "Expired"
    remaining = expires_at - now
    hours = int(remaining.total_seconds() // 3600)
    if hours >= 24:
        days = hours // 24
        return f"Pending · expires in {days}d"
    if hours >= 1:
        return f"Pending · expires in {hours}h"
    minutes = max(1, int(remaining.total_seconds() // 60))
    return f"Pending · expires in {minutes}m"


def _accounts():
    """Portal accounts with the state of their most recent invitation.

    One bounded query, not a per-account lookup: a correlated subquery picks the latest invitation id
    per account and the join reads only that row, so the table costs the same whether an account has
    one invitation or ten."""
    latest = (select(func.max(portal_invitations.c.id))
              .where(portal_invitations.c.portal_account_id == portal_accounts.c.id)
              .correlate(portal_accounts).scalar_subquery())
    now = datetime.now(timezone.utc)
    with engine.connect() as connection:
        rows = connection.execute(
            select(portal_accounts.c.id, portal_accounts.c.display_name, portal_accounts.c.email,
                   portal_accounts.c.status, portal_accounts.c.mfa_enabled,
                   portal_accounts.c.last_login_at, portal_accounts.c.person_id,
                   portal_invitations.c.created_at.label("invited_at"),
                   portal_invitations.c.expires_at, portal_invitations.c.accepted_at,
                   portal_invitations.c.revoked_at)
            .select_from(portal_accounts.outerjoin(
                portal_invitations, portal_invitations.c.id == latest))
            .order_by(portal_accounts.c.created_at.desc())).mappings().all()
    accounts = []
    for row in rows:
        account = {k: row[k] for k in ("id", "display_name", "email", "status", "mfa_enabled",
                                       "last_login_at", "person_id")}
        # Only the derived phrase reaches the template — no invitation id, token hash or timestamps.
        account["invitation_state"] = _invitation_state(row, now)
        accounts.append(account)
    return accounts


def _admin_page(request, principal, **extra):
    """Render the Client Portal administration page. ``extra`` carries one-shot review state
    (a duplicate warning and the values staff typed) so a refused creation comes back as normal
    UI instead of a redirect carrying client details in the URL."""
    # Imported in-function so the single canonical patch target for readiness stays
    # ``app.portal.gate.production_ready`` rather than a copy bound at import time.
    from app.portal.gate import production_ready

    q = request.query_params
    context = {"accounts": _accounts(), "principal": principal,
               # Read-only. The page states whether external access is still blocked; it never
               # changes a gate. production_ready() AND-gates portal.enabled, the compliance
               # sign-off and a production-capable IdP, so one value covers the whole condition.
               "production_ready": production_ready(),
               "invited": q.get("invited"), "error": q.get("error"),
               "revoked": q.get("revoked"),
               # Read once; gone on the next load.
               "activation": _take_activation_url(request),
               "access_choices": invite_targets.ACCESS_CHOICES,
               "rep_notice": invite_targets.AUTHORIZED_REPRESENTATIVE_NOTICE,
               "create_error": None, "create_review": None, "created": None}
    context.update(extra)
    return templates.TemplateResponse(request=request, name="admin/client_portal.html",
                                      context=context)


@router.get("", response_class=HTMLResponse)
def portal_admin_home(request: Request, principal: Principal = Depends(require_capability("client.read"))):
    return _admin_page(request, principal)


@router.get("/accounts")
def portal_admin_accounts(principal: Principal = Depends(require_capability("client.read"))):
    return {"accounts": _accounts()}


@router.get("/client-search")
def portal_admin_client_search(q: str = "", first_name: str = "", last_name: str = "",
                               email: str = "", phone: str = "",
                               principal: Principal = Depends(require_capability("client.read"))):
    """Search EXISTING Client360 people for the invite form.

    Staff type into the same four human fields the invitation itself uses — first name, last name,
    email, phone — and every non-empty one narrows the result. Record-scoped through the existing
    principal-scoped search, so staff can only find people they may service. Returns the
    human-readable fields needed to tell duplicate names apart; the id is carried only so the next
    request can name a selection, and is re-validated server-side then.

    Every parameter has a default, so a partially filled form can never produce a 422."""
    results = invite_targets.search_people(
        principal, q or None, first_name=first_name or None, last_name=last_name or None,
        email=email or None, phone=phone or None)
    return {"query": q, "first_name": first_name, "last_name": last_name, "email": email,
            "phone": phone, "results": results}


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
        person_id: str = Form(default=""), email: str = Form(default=""),
        access_type: str = Form(default=DEFAULT_ACCESS),
        principal: Principal = Depends(require_capability("client.write")),
        mail_transport=None):
    """Browser form over the SAME scoped/audited invite service as POST /invite. Post/Redirect/Get so a
    refresh never re-invites; a failure redirects with an error banner instead of a raw 403.

    Every parameter is OPTIONAL at the boundary on purpose. With ``Form(...)`` FastAPI validated the
    body BEFORE this function ran, so a submission missing ``person_id`` never reached the code below
    — it raised ``RequestValidationError`` and the default handler returned raw Pydantic JSON
    (``{"detail":[{"type":"missing","loc":["body","person_id"]...``) straight to the browser. Giving
    every field a default moves that case into the handler, where it becomes an ordinary banner on
    the admin page. Missing input is a normal staff mistake, not a protocol error.

    Staff submit a SELECTED PERSON and an email address — never an internal household or organization
    id. ``person_id`` arrives from the browser and is therefore treated as a claim, not an authority:
    :func:`invite_targets.resolve_invite_target` re-checks write record scope and derives the
    household from the database on every submission, so a tampered field fails closed."""
    if not (person_id or "").strip():
        # No selection: the same message the client-side guard shows, for anyone who bypasses it.
        return RedirectResponse(
            "/admin/client-portal?error=" + quote(NO_SELECTION_ERROR), status_code=303)
    try:
        target = invite_targets.resolve_invite_target(principal, person_id)
        access = invite_targets.validate_access_type(access_type, target)
    except invite_targets.InviteTargetError as exc:
        return RedirectResponse(f"/admin/client-portal?error={quote(str(exc))}", status_code=303)

    # Display name and household come from the resolved record; only the contact email is staff-set.
    # portal_accounts.email is a contact/display address, never an identity key — Microsoft sign-in
    # binds the immutable subject — so it may legitimately differ from people.primary_email.
    name = target.full_name
    household_id = target.household_id
    email = (email or "").strip() or target.email
    if not email:
        return RedirectResponse(
            "/admin/client-portal?error=" + quote("An email address is required to invite a client."),
            status_code=303)
    try:
        account_id, raw_token = invite_portal_account(
            person_id=target.person_id, household_id=household_id, email=email,
            display_name=name, access_type=access,
            invited_by_user_id=principal.user_id)
    except PortalAccountConflictError as exc:
        # A deliberate, staff-facing sentence from the service ("... already has an active portal
        # account. Revoke it first ..."). Safe to show verbatim — it contains no database detail.
        return RedirectResponse(f"/admin/client-portal?error={quote(str(exc))}", status_code=303)
    except Exception as exc:  # noqa: BLE001 — surface a friendly banner, never a stack trace
        # NEVER type(exc).__name__: that put "IntegrityError" in front of staff as if it were an
        # explanation. The class name goes to the log for an operator; staff get a sentence they can
        # act on.
        logger.warning("portal_admin_invite_failed exception=%s", type(exc).__name__)
        return RedirectResponse(
            "/admin/client-portal?error=" + quote(INVITE_FAILED_ERROR), status_code=303)
    # Metadata deliberately carries person/access only — NEVER the token or the activation URL.
    _audit(request, principal, "portal.admin.invited", account_id,
           {"person_id": target.person_id, "access_type": access, "via": "form"})
    # The activation URL exists only in this frame: it is emailed, stashed once for the staff
    # fallback, and then dropped. It is never persisted, logged, audited, or put in a redirect.
    try:
        activation_url = _activation_url(request, raw_token)
    except Exception:  # noqa: BLE001 — the account IS invited; only the link could not be built
        return RedirectResponse(f"/admin/client-portal?invited={quote(name)}"
                                "&error=Activation+link+unavailable%3A+check+PUBLIC_BASE_URL",
                                status_code=303)

    # Email is the normal delivery path; the one-time admin handoff remains the fallback for when
    # it is off, unconfigured, or refused.
    delivery = email_delivery.send_portal_invitation(
        recipient_email=email, display_name=name, activation_url=activation_url,
        request_id=request.state.request_id, graph_post=mail_transport)
    _audit(request, principal,
           "portal.admin.invitation_email_sent" if delivery.delivered
           else "portal.admin.invitation_email_failed",
           account_id,
           # Safe metadata only: status, provider, failure class, recipient DOMAIN. Never the
           # address itself, never the token, never the activation URL.
           {"person_id": target.person_id, "recipient_domain": delivery.recipient_domain,
            **delivery.audit_metadata})

    _remember_activation_url(request, display_name=name, url=activation_url, delivery=delivery)
    return RedirectResponse(f"/admin/client-portal?invited={quote(name)}", status_code=303)


@router.post("/create-client", response_class=HTMLResponse)
def portal_admin_create_client(
        request: Request,
        first_name: str = Form(default=""), last_name: str = Form(default=""),
        email: str = Form(default=""), phone: str = Form(default=""),
        acknowledge_duplicate: str = Form(default=""),
        principal: Principal = Depends(require_capability("client.write"))):
    """Create a canonical client from the details staff typed into the invite form.

    Every field is OPTIONAL at the boundary so FastAPI can never pre-empt this handler with raw
    Pydantic JSON; validation, authorization, normalisation and duplicate prevention all happen in
    :mod:`app.services.person_creation`, never here and never in JavaScript. There is no local
    ``people`` INSERT — the service owns creation, the household, the record assignment, and the
    audit/event/timeline provenance.

    Creating a client does NOT create an invitation. The new person becomes the selected target and
    staff must still choose an access level and press Send portal invitation."""
    typed = {"first_name": (first_name or "").strip(), "last_name": (last_name or "").strip(),
             "email": (email or "").strip(), "phone": (phone or "").strip()}
    try:
        created = person_creation.create_client(
            principal, request_id=request.state.request_id,
            acknowledge_name_duplicate=bool((acknowledge_duplicate or "").strip()),
            require_email=True,                  # the portal cannot invite without one
            **typed)
    except person_creation.PossibleDuplicateWarning as warning:
        # Not an error: staff must look at the candidates and decide. Re-rendered rather than
        # redirected so no client detail travels in a URL.
        return _admin_page(request, principal, create_error=str(warning),
                           create_review={**typed, "candidates": warning.candidates,
                                          "needs_acknowledgement": True,
                                          "hard_duplicate": False})
    except person_creation.DuplicateClientError as blocked:
        # An exact email/phone match. Creation stays REFUSED — this branch offers no
        # acknowledgement and the template renders no create action for it — but the candidates it
        # carries make the existing record selectable, which is the whole point of telling staff it
        # exists. Out of scope, ``candidates`` is empty and the page shows only the generic refusal.
        return _admin_page(request, principal, create_error=str(blocked),
                           create_review={**typed, "candidates": blocked.candidates,
                                          "needs_acknowledgement": False,
                                          "hard_duplicate": True})
    except person_creation.PersonCreationError as exc:
        return _admin_page(request, principal, create_error=str(exc),
                           create_review={**typed, "candidates": [],
                                          "needs_acknowledgement": False,
                                          "hard_duplicate": False})

    _audit(request, principal, "portal.admin.client_created", created.person_id,
           {"via": "portal_admin", "household_created": created.household_created})
    return _admin_page(request, principal, created={
        "person_id": created.person_id, "first_name": created.first_name,
        "last_name": created.last_name, "full_name": created.full_name,
        "email": created.email, "phone": created.phone,
        "household_name": created.household_name})


@router.post("/accounts/{account_id}/revoke", status_code=200)
def portal_admin_revoke(account_id: int, request: Request,
                        principal: Principal = Depends(require_capability("client.write"))):
    """Revoke one portal account. Same route for the browser form and for API callers.

    Revocation now closes out the WHOLE of the account's access, not just its status: outstanding
    invitations and live sessions are revoked alongside the access grants. Leaving an unaccepted
    invitation live meant a revoked client could still activate from the email already in their
    inbox; leaving a session live meant an open browser kept working until the session expired.
    :func:`revoke_account_access` performs all three inside this transaction, and is the same
    function a re-invitation uses, so the two paths cannot diverge.

    The account row itself is never deleted — it is the audit trail, and it is what a later
    re-invitation reuses.

    Browsers get a redirect back to the admin page with a banner; the JSON body is preserved
    verbatim for non-HTML callers. Staff previously saw the raw JSON body rendered in the address
    bar after clicking Revoke."""
    from app.portal.service import revoke_account_access

    def _fail(status: int, message: str):
        """A browser gets the admin page with an error banner; an API caller gets its HTTPException."""
        if wants_html(request):
            return RedirectResponse(f"/admin/client-portal?error={quote(message)}", status_code=303)
        raise HTTPException(status, message)

    with engine.begin() as connection:
        acct = connection.execute(select(portal_accounts.c.person_id,
                                         portal_accounts.c.display_name).where(
            portal_accounts.c.id == account_id)).mappings().one_or_none()
        if not acct:
            return _fail(404, "Portal account not found")
        if not record_in_scope(principal, "person", acct["person_id"], write=True):
            return _fail(403, "Person is outside your record scope")
        connection.execute(portal_accounts.update().where(portal_accounts.c.id == account_id).values(
            status="revoked", updated_at=datetime.now(timezone.utc)))
        closed = revoke_account_access(connection, account_id)
    _audit(request, principal, "portal.admin.revoked", account_id, closed)
    if wants_html(request):
        return RedirectResponse(
            "/admin/client-portal?revoked=" + quote(acct["display_name"] or "the account"),
            status_code=303)
    return {"account_id": account_id, "status": "revoked", **closed}


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


@router.post("/threads/new")
def portal_admin_start_thread(
        request: Request,
        person_id: str = Form(default=""), subject: str = Form(default=""),
        body: str = Form(default=""), topic: str = Form(default=""),
        principal: Principal = Depends(require_capability("client.write"))):
    """Staff open a conversation with a client they are authorised to service.

    ``client.write`` and not ``client.read``: starting a conversation writes to the client's record,
    so it matches the reply route rather than the read-only inbox. Every field is optional at the
    boundary so FastAPI cannot pre-empt this handler with raw Pydantic JSON; validation and
    authorization live in :func:`communication_hub.staff_start_thread`, which re-resolves the person
    and re-checks write record scope. The browser never chooses the sender — the Principal does.

    A client with no usable portal account produces a plain explanation on the inbox, never a
    silently created account and never an automatic invitation."""
    if not (person_id or "").strip().isdigit():
        return RedirectResponse(
            "/admin/client-portal/threads?error=" + quote(hub.NO_CLIENT_SELECTED), 303)
    try:
        thread_id = hub.staff_start_thread(
            principal, person_id=int(person_id), subject=subject, body=body,
            topic=(topic or "").strip() or None, request_id=request.state.request_id)
    except hub.StaffMessageError as exc:
        return RedirectResponse(f"/admin/client-portal/threads?error={quote(str(exc))}", 303)
    return RedirectResponse(f"/admin/client-portal/threads/{thread_id}"
                            "?notice=" + quote("Conversation started."), 303)


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
