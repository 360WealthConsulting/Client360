from typing import Optional
from urllib.parse import quote
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db import engine, portal_document_requests, portal_notifications, portal_sessions, portal_threads
from app.portal.service import (PortalPrincipal, accept_invitation, client_action_detail,
    client_action_needed, client_document_requests,
    client_documents, client_notifications, client_tasks, client_threads, complete_client_task,
    confirm_request_upload, create_portal_session, create_thread, dashboard,
    employer_action_detail, employer_action_needed, employer_census_upload, employer_organization_ids,
    list_messages, mark_read, portal_scope, request_password_reset, consume_password_reset,
    revoke_portal_session, send_message, require_scope)
from app.portal import appointments as portal_appointments
from app.portal import communication_hub as portal_hub
from app.portal import consent as portal_consent
from app.portal import profile as portal_profile
from app.portal import vault_documents as portal_vault
from app.services.vault.service import CATEGORIES as VAULT_CATEGORIES
from app.services.vault.storage import VaultStorageError
from app.portal.financial import financial_summary
from app.portal.gate import gate as portal_runtime_gate
from app.services.documents import save_person_document
from app.services.exception_engine import ExceptionNotFoundError
from app.services import insurance_portal
from app.portal.providers import PORTAL_IDENTITY_PROVIDERS

router = APIRouter(tags=["client-portal"])
templates = Jinja2Templates(directory="app/templates")

def current_portal(request: Request):
    principal = getattr(request.state, "portal_principal", None)
    if not principal: raise HTTPException(401, "Portal authentication required")
    return principal

class InvitationAcceptance(BaseModel):
    token: str; identity_provider: str; identity_assertion: str; device_fingerprint: str; device_name: Optional[str] = None
class PasswordResetRequest(BaseModel): email: str
class PasswordResetConsume(BaseModel): token: str
class ThreadCreate(BaseModel): household_id: int; person_id: int; subject: str; body: str
class MessageCreate(BaseModel): body: str; attachment_document_ids: list[int] = Field(default_factory=list)
class NotificationCreate(BaseModel): notification_type: str; title: str; body: Optional[str] = None; idempotency_key: str
class ConsentAction(BaseModel): consent_type: str; version: str = "v1"; accepted: bool = True
class ConsentWithdraw(BaseModel): consent_type: str
class AppointmentRequest(BaseModel): person_id: int; household_id: int; preferred_window: str | None = None; reason: str | None = None

def _production_provider_key():
    """The provider key used for real external sign-in."""
    from app.portal.identity_microsoft import MICROSOFT_PROVIDER_KEY
    return MICROSOFT_PROVIDER_KEY


def _production_identity_provider_available() -> bool:
    """Whether the REAL external identity provider is registered and usable for sign-in.

    Deliberately NOT ``production_ready()``: that also requires ``portal.enabled`` and
    ``portal.production_signed_off``, which govern whether client data may be exposed at all. Whether
    anyone CAN authenticate and whether the portal is authorized to serve data are separate concerns,
    and conflating them is what left a fully configured IdP behind a "configuration is required"
    message. The gates still apply on every authenticated request, and ``/portal/auth/start`` repeats
    this lookup and fails closed on its own.

    The local/test provider can never satisfy this: it registers under a different key and is
    ``production_capable = False``, so it is excluded by ``production_capable()``."""
    return _production_provider_key() in PORTAL_IDENTITY_PROVIDERS.production_capable()


@router.get("/portal/login", response_class=HTMLResponse)
def portal_login(request: Request, invitation: str | None = None):
    """Public sign-in landing page.

    Renders the external sign-in action only when the production provider is actually registered;
    otherwise it keeps the existing fail-closed configuration message. An optional ``invitation``
    token is forwarded to ``/portal/auth/start`` unchanged — that route holds it server-side in the
    browser session and never passes it to the IdP.
"""
    auth_start_url = "/portal/auth/start"
    if invitation:
        auth_start_url += f"?invitation={quote(invitation, safe='')}"
    return templates.TemplateResponse(request=request, name="portal/login.html", context={
        "identity_provider_available": _production_identity_provider_available(),
        "auth_start_url": auth_start_url,
    })

@router.get("/portal/auth/start")
def portal_auth_start(request: Request, invitation: str | None = None):
    """Begin external sign-in: mint state/nonce/PKCE server-side and redirect to the IdP.

    ``state``, ``nonce`` and the PKCE verifier are held in the browser session and never given to the
    client as parameters, so a callback cannot be replayed into a different session. An optional
    ``invitation`` token is carried in the session (not the redirect) for first-time activation."""
    import base64
    import hashlib
    import secrets

    from app.portal.providers import PORTAL_IDENTITY_PROVIDERS
    from app.security.origin import CanonicalOriginError, external_url

    try:
        provider = PORTAL_IDENTITY_PROVIDERS.get(_production_provider_key())
        # Built from the configured canonical origin, never the inbound Host header: the IdP matches
        # this string exactly and the token exchange must repeat it byte for byte.
        redirect_uri = external_url(request, "portal_auth_callback")
    except (ValueError, CanonicalOriginError):
        return RedirectResponse("/portal/login?error=unavailable", 303)

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    request.session["portal_oidc_state"] = state
    request.session["portal_oidc_nonce"] = nonce
    request.session["portal_oidc_verifier"] = verifier
    if invitation:
        request.session["portal_oidc_invitation"] = invitation
    try:
        url = provider.authorization_url(
            state=state, nonce=nonce, redirect_uri=redirect_uri,
            code_challenge=challenge)
    except Exception:
        return RedirectResponse("/portal/login?error=unavailable", 303)
    return RedirectResponse(url, 303)


@router.get("/portal/auth/callback", name="portal_auth_callback")
def portal_auth_callback(request: Request, code: str | None = None, state: str | None = None):
    """Validate the callback, bind the immutable subject and establish a portal session.

    Every failure returns the SAME generic redirect: the reason (bad state, bad token, missing MFA,
    unknown subject, revoked account) is never disclosed to the browser. No token material is logged."""
    import secrets as _secrets

    from app.portal.providers import PORTAL_IDENTITY_PROVIDERS
    from app.portal.service import sign_in_with_subject
    from app.security.origin import external_url

    expected_state = request.session.pop("portal_oidc_state", "")
    nonce = request.session.pop("portal_oidc_nonce", "")
    verifier = request.session.pop("portal_oidc_verifier", "")
    invitation = request.session.pop("portal_oidc_invitation", None)

    if not code or not state or not expected_state \
            or not _secrets.compare_digest(state, expected_state):
        return RedirectResponse("/portal/login?error=failed", 303)      # CSRF / replay / no flow open
    try:
        provider = PORTAL_IDENTITY_PROVIDERS.get(_production_provider_key())
        # Identical construction to /portal/auth/start — the IdP rejects the exchange otherwise.
        identity = provider.exchange_code(
            code=code, redirect_uri=external_url(request, "portal_auth_callback"),
            code_verifier=verifier, expected_nonce=nonce)
        if invitation:
            account_id = accept_invitation(invitation, identity.subject, identity.mfa_verified)
        else:
            account_id = sign_in_with_subject(identity.subject, identity.mfa_verified)
        token = create_portal_session(
            account_id, device_fingerprint=request.headers.get("user-agent", "portal"),
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"))
    except Exception:
        return RedirectResponse("/portal/login?error=failed", 303)
    request.session["portal_session_token"] = token                     # fresh session id post-auth
    return RedirectResponse("/portal", 303)


@router.post("/api/v1/portal/auth/invitations/accept")
def invitation_accept(payload: InvitationAcceptance, request: Request):
    try:
        identity = PORTAL_IDENTITY_PROVIDERS.get(payload.identity_provider).verify_activation(payload.identity_assertion)
        account_id = accept_invitation(payload.token, identity.subject, identity.mfa_verified)
        token = create_portal_session(account_id, device_fingerprint=payload.device_fingerprint, device_name=payload.device_name, ip_address=request.client.host if request.client else None, user_agent=request.headers.get("user-agent"))
    except ValueError as exc: raise HTTPException(400, str(exc))
    request.session["portal_session_token"] = token
    return {"account_id": account_id, "mfa_verified": identity.mfa_verified}

@router.post("/api/v1/portal/auth/password-reset/request", status_code=202)
def password_reset_request(payload: PasswordResetRequest):
    request_password_reset(payload.email)
    return {"status": "accepted"}

@router.post("/api/v1/portal/auth/password-reset/consume")
def password_reset_consume(payload: PasswordResetConsume):
    try: account_id = consume_password_reset(payload.token)
    except ValueError as exc: raise HTTPException(400, str(exc))
    return {"account_id": account_id, "handoff": "identity_provider"}

@router.post("/api/v1/portal/auth/logout", status_code=204)
def portal_logout(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    revoke_portal_session(request.session.pop("portal_session_token", None))

# Client "Action Needed": client-visible tax exceptions surfaced as plain-language,
# scoped, portal-safe action items. Declared before the catch-all page route so the
# static path wins. Reads only through the canonical Exception Engine projection.
@router.get("/portal/action-needed", response_class=HTMLResponse)
def portal_action_needed(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    return templates.TemplateResponse(request=request, name="portal/action_needed.html",
        context={"action_items": client_action_needed(principal), "principal": principal})

@router.get("/api/v1/portal/exceptions")
def api_portal_exceptions(principal: PortalPrincipal = Depends(current_portal)):
    return {"action_items": client_action_needed(principal)}

@router.get("/api/v1/portal/exceptions/{exception_id}")
def api_portal_exception(exception_id: int, principal: PortalPrincipal = Depends(current_portal)):
    try:
        return client_action_detail(principal, exception_id)
    except ExceptionNotFoundError:
        raise HTTPException(404, "Action item not found")

# Employer portal — benefits "Action Needed" (organization-scoped, employer-safe, PII-free).
# Declared before the catch-all page route. Read-only on exceptions; the employer acts through
# census upload and secure messages. Out-of-scope organizations deny existence with 404.
@router.get("/portal/benefits/action-needed", response_class=HTMLResponse)
def portal_employer_action_needed(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    return templates.TemplateResponse(request=request, name="portal/benefits_action_needed.html",
        context={"action_items": employer_action_needed(principal), "principal": principal})

@router.get("/api/v1/portal/benefits/organizations")
def api_portal_employer_orgs(principal: PortalPrincipal = Depends(current_portal)):
    return {"organization_ids": employer_organization_ids(principal)}

@router.get("/api/v1/portal/benefits/exceptions")
def api_portal_employer_exceptions(principal: PortalPrincipal = Depends(current_portal)):
    return {"action_items": employer_action_needed(principal)}

@router.get("/api/v1/portal/benefits/exceptions/{exception_id}")
def api_portal_employer_exception(exception_id: int, principal: PortalPrincipal = Depends(current_portal)):
    from app.services.exception_engine import ExceptionNotFoundError
    try:
        return employer_action_detail(principal, exception_id)
    except ExceptionNotFoundError:
        raise HTTPException(404, "Action item not found")

@router.post("/api/v1/portal/benefits/census/upload", status_code=201)
async def api_portal_census_upload(organization_id: int, file: UploadFile = File(...),
                                   principal: PortalPrincipal = Depends(current_portal)):
    try:
        document_id = employer_census_upload(principal, organization_id, original_name=file.filename,
                                             source=file.file, content_type=file.content_type)
    except PermissionError:
        raise HTTPException(404, "Organization not found") from None   # scope denial never discloses existence
    except VaultStorageError as exc:
        raise HTTPException(400, str(exc)) from exc                    # bad extension / oversize / content mismatch / empty
    finally:
        await file.close()
    return {"document_id": document_id, "status": "uploaded"}

# --- Insurance policyholder surface (Phase 7) — read-only, org/person-scoped via the EXISTING
# portal grants (permission='insurance'). Out-of-scope policies deny existence with 404. No
# producers, commissions, licensing, or exceptions are ever exposed. Declared before the
# /portal/{page} catch-all so /portal/insurance resolves here. ---
@router.get("/portal/insurance", response_class=HTMLResponse)
def portal_insurance(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    policies = insurance_portal.portal_policies(principal)
    return templates.TemplateResponse(request=request, name="portal/insurance.html",
                                      context={"policies": policies, "principal": principal})


@router.get("/api/v1/portal/insurance/policies")
def api_portal_insurance_policies(principal: PortalPrincipal = Depends(current_portal)):
    return {"policies": insurance_portal.portal_policies(principal)}


@router.get("/api/v1/portal/insurance/policies/{policy_id}")
def api_portal_insurance_policy(policy_id: int, principal: PortalPrincipal = Depends(current_portal)):
    detail = insurance_portal.portal_policy_detail(principal, policy_id)
    if detail is None:
        raise HTTPException(404, "Policy not found")  # out-of-scope never discloses existence
    return detail


# --- D.43 external surfaces (declared before the /portal/{page} catch-all). All minimized, reusing the
# authoritative services; every mutation delegates. ---

@router.get("/portal/financial", response_class=HTMLResponse)
def portal_financial(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    return templates.TemplateResponse(request=request, name="portal/financial.html",
                                      context={"summary": financial_summary(principal), "principal": principal})

@router.get("/api/v1/portal/financial")
def api_portal_financial(principal: PortalPrincipal = Depends(current_portal)):
    return financial_summary(principal)

@router.get("/portal/preferences", response_class=HTMLResponse)
def portal_preferences(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    return templates.TemplateResponse(request=request, name="portal/preferences.html",
        context={"consents": portal_consent.list_consents(principal.account_id), "principal": principal})

@router.get("/api/v1/portal/consents")
def api_portal_consents(principal: PortalPrincipal = Depends(current_portal)):
    return {"consents": portal_consent.list_consents(principal.account_id)}

@router.post("/api/v1/portal/consents", status_code=201)
def api_portal_consent_record(payload: ConsentAction, request: Request, principal: PortalPrincipal = Depends(current_portal)):
    try:
        cid = portal_consent.record_consent(principal.account_id, payload.consent_type, payload.version,
                                            request_id=request.state.request_id, accepted=payload.accepted)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": cid, "consent_type": payload.consent_type, "version": payload.version, "accepted": payload.accepted}

@router.post("/api/v1/portal/consents/withdraw", status_code=200)
def api_portal_consent_withdraw(payload: ConsentWithdraw, request: Request, principal: PortalPrincipal = Depends(current_portal)):
    wid = portal_consent.withdraw_consent(principal.account_id, payload.consent_type, request_id=request.state.request_id)
    if wid is None:
        raise HTTPException(404, "No active consent to withdraw")
    return {"id": wid, "consent_type": payload.consent_type, "state": "withdrawn"}

@router.get("/portal/security", response_class=HTMLResponse)
def portal_security(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    with engine.connect() as connection:
        sessions = connection.execute(select(
            portal_sessions.c.created_at, portal_sessions.c.last_seen_at, portal_sessions.c.expires_at,
            portal_sessions.c.ip_address).where(
            portal_sessions.c.portal_account_id == principal.account_id,
            portal_sessions.c.revoked_at.is_(None)).order_by(portal_sessions.c.last_seen_at.desc()).limit(20)).mappings().all()
    return templates.TemplateResponse(request=request, name="portal/security.html", context={
        "sessions": [dict(s) for s in sessions],
        "consents": portal_consent.list_consents(principal.account_id), "principal": principal})

@router.get("/api/v1/portal/appointments")
def api_portal_appointments(principal: PortalPrincipal = Depends(current_portal)):
    # Upcoming appointments are the scheduling-owned calendar_event timeline already assembled by dashboard.
    return {"meetings": [dict(m) for m in dashboard(principal)["meetings"]]}

@router.get("/portal/engagement", response_class=HTMLResponse)
def portal_engagement_page(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    # Unified recent-interactions surface for the client — composed from the D.43 portal scoped reads by
    # the D.44 engagement layer (no new portal subsystem). Gated by portal.timeline.enabled (opt-in).
    from app.services.communications.engagement import portal_engagement
    return templates.TemplateResponse(request=request, name="portal/engagement.html",
                                      context={"engagement": portal_engagement(principal), "principal": principal})

@router.get("/api/v1/portal/engagement")
def api_portal_engagement(principal: PortalPrincipal = Depends(current_portal)):
    from app.services.communications.engagement import portal_engagement
    return portal_engagement(principal)

@router.post("/api/v1/portal/appointments/request", status_code=201)
def api_portal_appointment_request(payload: AppointmentRequest, principal: PortalPrincipal = Depends(current_portal)):
    try:
        thread_id = portal_appointments.request_appointment(principal, person_id=payload.person_id,
            household_id=payload.household_id, preferred_window=payload.preferred_window, reason=payload.reason)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"thread_id": thread_id, "status": "requested"}

@router.get("/api/v1/portal/documents/{document_id}/download")
def api_portal_document_download(request: Request, document_id: int,
                                 principal: PortalPrincipal = Depends(current_portal)):
    """Deliver a client-visible VAULT document. Never a canonical staff document.

    This route previously read the canonical ``documents`` table and authorized on
    ``documents.person_id`` + ``require_scope`` alone. That table has no ``client_visible`` column, so a
    client with a documents grant could download ANY non-archived canonical document filed against their
    own person — internal notes included — because person scope was the only barrier. It also bypassed
    the ``portal.document.downloaded`` audit event.

    Authorization now belongs entirely to ``portal_vault.download_document``, the single authoritative
    policy: vault-backed row, ``client_visible`` true, ``vault_document_links`` intersecting the
    documents-permission scope, downloadable status (or the client's own pending upload), and the audit
    event on success. There is NO fallback to the canonical table — a vault miss fails closed.

    Denial contract: every resource-level failure — unknown id, out-of-scope, canonical-only,
    not client-visible, unapproved status, missing file — returns an identical generic 404, so a client
    cannot distinguish "does not exist" from "exists but is not yours". The surface gate stays 403
    (feature unavailable), which is a different fact from resource inaccessibility.
    """
    from fastapi.responses import FileResponse

    if not portal_runtime_gate("portal.documents.download_enabled"):
        # Feature unavailable is NOT a resource-existence answer; the middleware returns the same 403
        # before this route runs. Kept here so a direct call cannot bypass the surface gate.
        raise HTTPException(403, "This feature is not available on your account.")
    try:
        path, filename, mime = portal_vault.download_document(
            principal, document_id,
            request_id=getattr(request.state, "request_id", "portal"),
            ip_address=request.client.host if request.client else None)
    except PermissionError as exc:
        raise HTTPException(404, "Document not found") from exc
    if not path.is_file():
        raise HTTPException(404, "Document not found")
    return FileResponse(str(path), media_type=mime or "application/octet-stream", filename=filename)


# --- Browser (HTML) client surfaces over the EXISTING portal services --------
# These render polished pages and use Post/Redirect/Get for mutations. They reuse the
# same scoped, audited services as the JSON APIs — no parallel implementation. Declared
# before the /portal/{page} catch-all so the specific paths win.

@router.post("/portal/logout")
def portal_logout_browser(request: Request):
    """Browser sign-out: revoke the portal session, then redirect to the login page.
    Safe for an unauthenticated POST (idempotent) so a stale tab can always sign out."""
    revoke_portal_session(request.session.pop("portal_session_token", None))
    request.session.clear()
    return RedirectResponse("/portal/login", status_code=303)


@router.get("/portal/documents", response_class=HTMLResponse)
def portal_documents_page(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    """Client-visible Vault documents + open document requests. Reads only through the
    scoped Vault↔Portal bridge and the request service."""
    return templates.TemplateResponse(request=request, name="portal/documents.html", context={
        "principal": principal,
        "documents": portal_vault.portal_documents(principal),
        "requests": client_document_requests(principal),
        "notice": request.query_params.get("notice"),
        "error": request.query_params.get("error")})


@router.get("/portal/upload", response_class=HTMLResponse)
def portal_upload_page(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    return templates.TemplateResponse(request=request, name="portal/upload.html", context={
        "principal": principal, "categories": VAULT_CATEGORIES,
        "request_id": request.query_params.get("request_id"),
        "error": request.query_params.get("error")})


@router.post("/portal/upload")
async def portal_upload_submit(
        request: Request,
        file: UploadFile = File(...),
        display_name: str = Form(...),
        category: str = Form("general"),
        request_id: int | None = Form(None),
        principal: PortalPrincipal = Depends(current_portal)):
    """Browser upload over the SAME secure client-upload backend (portal_vault.upload_document):
    the file lands as a PENDING vault document scoped to the account's person, awaiting employee
    approval — authorization, ownership, storage and audit are all enforced by the service.
    Post/Redirect/Get: success → documents page; failure → back to the form with a banner."""
    name = (display_name or file.filename or "").strip()
    if not name:
        return RedirectResponse("/portal/upload?error=Please+name+the+document", status_code=303)
    try:
        portal_vault.upload_document(
            principal, source=file.file, original_filename=file.filename, display_name=name,
            category=category, request_id=request_id,
            http_request_id=getattr(request.state, "request_id", "portal"),
            ip_address=request.client.host if request.client else None)
    except PermissionError:
        # Scope denial: never reveal entity existence; friendly banner, no stack trace.
        return RedirectResponse("/portal/upload?error=You+are+not+able+to+upload+this+document",
                                status_code=303)
    except (VaultStorageError, ValueError):
        return RedirectResponse("/portal/upload?error=That+file+could+not+be+accepted",
                                status_code=303)
    finally:
        await file.close()
    return RedirectResponse(
        "/portal/documents?notice=" + quote("Document uploaded — your advisor will review it shortly."),
        status_code=303)


@router.get("/portal/messages", response_class=HTMLResponse)
def portal_messages_page(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    """List the client's conversations (topic, unread staff response, linked requests) + compose form."""
    return templates.TemplateResponse(request=request, name="portal/messages.html", context={
        "principal": principal, "threads": portal_hub.client_conversations(principal),
        "topics": portal_hub.TOPICS,
        "notice": request.query_params.get("notice"), "error": request.query_params.get("error")})


@router.post("/portal/messages/new")
def portal_messages_new(request: Request, subject: str = Form(...), body: str = Form(...),
                        topic: str | None = Form(None),
                        principal: PortalPrincipal = Depends(current_portal)):
    """Start a new thread on the client's OWN record. Person/household are derived server-side from
    the account's messages scope — never accepted from the client — so a client cannot open a thread
    against someone else's record. create_thread re-checks scope (permission='messages')."""
    if not (subject or "").strip() or not (body or "").strip():
        return RedirectResponse("/portal/messages?error=" + quote("A subject and message are required."),
                                status_code=303)
    scope = portal_scope(principal.account_id, permission="messages")
    household_id = next(iter(scope["household_ids"]), None)
    if household_id is None or principal.person_id not in scope["person_ids"]:
        return RedirectResponse("/portal/messages?error=" + quote("Messaging is not enabled on your account."),
                                status_code=303)
    safe_topic = topic if topic in portal_hub.TOPICS else None      # client-chosen topic; else unassigned
    try:
        thread_id = create_thread(principal, household_id=household_id, person_id=principal.person_id,
                                  subject=subject.strip(), body=body.strip(), topic=safe_topic)
    except PermissionError:
        return RedirectResponse("/portal/messages?error=" + quote("Messaging is not enabled on your account."),
                                status_code=303)
    return RedirectResponse(f"/portal/messages/{thread_id}?notice=" + quote("Message sent."), status_code=303)


@router.get("/portal/messages/{thread_id}", response_class=HTMLResponse)
def portal_message_thread_page(thread_id: int, request: Request,
                               principal: PortalPrincipal = Depends(current_portal)):
    """Show one thread's client-visible messages (internal staff notes are never returned by
    list_messages). Out-of-scope threads deny existence with 404."""
    try:
        messages = list_messages(principal, thread_id)          # client-visible only; internal notes never returned
    except PermissionError:
        raise HTTPException(404, "Conversation not found") from None
    with engine.connect() as connection:
        row = connection.execute(select(portal_threads.c.subject, portal_threads.c.topic).where(
            portal_threads.c.id == thread_id)).mappings().one_or_none()
    portal_hub.mark_thread_read_client(principal, thread_id)     # relationship-level client read marker
    return templates.TemplateResponse(request=request, name="portal/message_thread.html", context={
        "principal": principal, "thread_id": thread_id,
        "subject": (row["subject"] if row else None) or "Conversation",
        "topic": row["topic"] if row else None, "messages": messages,
        "linked_requests": portal_hub.linked_requests(thread_id),
        "notice": request.query_params.get("notice"), "error": request.query_params.get("error")})


@router.post("/portal/messages/{thread_id}/reply")
def portal_message_reply(thread_id: int, request: Request, body: str = Form(...),
                         principal: PortalPrincipal = Depends(current_portal)):
    """Reply into an existing thread via the scoped send_message service (permission='messages').
    Out-of-scope threads deny existence with 404."""
    if not (body or "").strip():
        return RedirectResponse(f"/portal/messages/{thread_id}?error=" + quote("Please enter a reply."),
                                status_code=303)
    try:
        send_message(principal, thread_id, body.strip())
    except PermissionError:
        raise HTTPException(404, "Conversation not found") from None
    return RedirectResponse(f"/portal/messages/{thread_id}?notice=" + quote("Reply sent."), status_code=303)


@router.get("/portal/profile", response_class=HTMLResponse)
def portal_profile_page(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    """View + edit the fields the client is authorized to change (contact details + contact
    preference). Reads through the profile service; never exposes internal identifiers."""
    return templates.TemplateResponse(request=request, name="portal/profile.html", context={
        "principal": principal,
        "profile": portal_profile.get_profile(principal),
        "notice": request.query_params.get("notice"),
        "error": request.query_params.get("error")})


@router.post("/portal/profile")
def portal_profile_submit(
        request: Request,
        email: str | None = Form(None),
        phone: str | None = Form(None),
        address: str | None = Form(None),
        city: str | None = Form(None),
        state: str | None = Form(None),
        postal_code: str | None = Form(None),
        preferred_contact_method: str | None = Form(None),
        principal: PortalPrincipal = Depends(current_portal)):
    """Apply a client's profile edits via the audited profile service, then Post/Redirect/Get.
    Only non-empty submitted values become candidate changes; the service allowlist
    (``_PERSON_FIELDS``/``_ACCOUNT_FIELDS``) is the authority on what a client may modify, so any
    protected field (e.g. legal name) is dropped server-side even if a forged field is posted."""
    submitted = {"email": email, "phone": phone, "address": address, "city": city, "state": state,
                 "postal_code": postal_code, "preferred_contact_method": preferred_contact_method}
    changes = {k: v.strip() for k, v in submitted.items() if v is not None and v.strip()}
    result = portal_profile.update_profile(
        principal, changes, request_id=getattr(request.state, "request_id", "portal"),
        ip_address=request.client.host if request.client else None)
    msg = "Your profile was updated." if result.get("changed") else "No changes were made."
    return RedirectResponse("/portal/profile?notice=" + quote(msg), status_code=303)


PAGE_NAMES = {"": "dashboard", "messages": "messages", "documents": "documents", "requests": "requests", "tasks": "tasks", "notifications": "notifications", "settings": "settings"}


@router.get("/portal", response_class=HTMLResponse)
def portal_home(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    """The authenticated client landing page.

    ``/portal/`` already rendered the dashboard -- ``PAGE_NAMES[""]`` -- but the bare ``/portal``
    matched no route, because the catch-all pattern requires the trailing slash. This is that one
    missing URL and nothing more.

    It delegates to :func:`portal_page` rather than repeating its body, so the landing page cannot
    drift from the page the catch-all serves: same dashboard read model, same template, same
    context. Authentication, portal scope and the feature gate are unchanged -- ``current_portal``
    supplies the PortalPrincipal the middleware already resolved, and the middleware applies
    ``portal_gate`` to every ``/portal`` path before the route runs.
    """
    return portal_page("", request, principal)


@router.get("/portal/{page:path}", response_class=HTMLResponse)
def portal_page(page: str, request: Request, principal: PortalPrincipal = Depends(current_portal)):
    if page not in PAGE_NAMES: raise HTTPException(404, "Portal page not found")
    data = dashboard(principal)
    return templates.TemplateResponse(request=request, name=f"portal/{PAGE_NAMES[page]}.html", context={"portal": data, "principal": principal})

@router.get("/api/v1/portal/dashboard")
def api_dashboard(principal: PortalPrincipal = Depends(current_portal)): return dashboard(principal)
@router.get("/api/v1/portal/profile")
def api_profile(principal: PortalPrincipal = Depends(current_portal)):
    """Client profile — the SAME projection v0 serves. This returned ``principal.__dict__``, which
    exposed the internal ``account_id`` and ``person_id`` and diverged from v0's deliberate contract."""
    return portal_profile.get_profile(principal)
@router.get("/api/v1/portal/messages")
def api_threads(principal: PortalPrincipal = Depends(current_portal)): return {"threads": client_threads(principal)}
@router.post("/api/v1/portal/messages", status_code=201)
def api_create_thread(payload: ThreadCreate, principal: PortalPrincipal = Depends(current_portal)):
    try: return {"id": create_thread(principal, **payload.dict())}
    except PermissionError as exc: raise HTTPException(403, str(exc))
@router.get("/api/v1/portal/messages/{thread_id}")
def api_messages(thread_id: int, principal: PortalPrincipal = Depends(current_portal)):
    try: return {"messages": list_messages(principal, thread_id)}
    except PermissionError as exc: raise HTTPException(403, str(exc))
@router.post("/api/v1/portal/messages/{thread_id}", status_code=201)
def api_send_message(thread_id: int, payload: MessageCreate, principal: PortalPrincipal = Depends(current_portal)):
    try: return {"id": send_message(principal, thread_id, payload.body, payload.attachment_document_ids)}
    except PermissionError as exc: raise HTTPException(403, str(exc))
@router.post("/api/v1/portal/messages/{message_id}/read", status_code=201)
def api_mark_read(message_id: int, principal: PortalPrincipal = Depends(current_portal)):
    try: return {"id": mark_read(principal, message_id)}
    except (ValueError, PermissionError) as exc: raise HTTPException(403, str(exc))

@router.get("/api/v1/portal/documents")
def api_documents(principal: PortalPrincipal = Depends(current_portal)): return {"documents": client_documents(principal)}
@router.get("/api/v1/portal/requests")
def api_requests(principal: PortalPrincipal = Depends(current_portal)): return {"requests": client_document_requests(principal)}
@router.post("/api/v1/portal/requests/{request_id}/upload", status_code=201)
async def api_request_upload(request_id: int, file: UploadFile = File(...), principal: PortalPrincipal = Depends(current_portal)):
    # Legacy request-fulfilment upload. Storage stays in the `documents` table because the request's
    # uploaded_document_id / document_versions FKs and confirm_request_upload are documents-bound (a
    # vault doc id could not satisfy those FKs), so it cannot route through portal_vault.upload_document
    # without breaking fulfilment. Instead it now applies the SAME client-upload controls via
    # save_person_document(verify_content=True): extension allow-list, streamed size cap, content/
    # magic-byte validation, filename/path safety and SHA-256. Scope + confirmation semantics preserved.
    with engine.connect() as connection: row = connection.execute(select(portal_document_requests).where(portal_document_requests.c.id == request_id)).mappings().one_or_none()
    if not row: raise HTTPException(404, "Document request not found")
    try: require_scope(principal, person_id=row["person_id"], household_id=row["household_id"], permission="documents")
    except PermissionError as exc: raise HTTPException(403, str(exc)) from exc
    try:
        document_id = save_person_document(person_id=row["person_id"], original_name=file.filename or "portal-upload", source=file.file, content_type=file.content_type, category="portal_request", description=row["title"], uploaded_by=principal.display_name, verify_content=True)
    except VaultStorageError as exc:
        raise HTTPException(400, str(exc)) from exc      # bad extension / oversize / content mismatch / empty
    finally:
        await file.close()
    try: version = confirm_request_upload(principal, request_id, document_id)
    except PermissionError as exc: raise HTTPException(403, str(exc)) from exc
    return {"document_id": document_id, "version": version, "status": "uploaded"}

@router.get("/api/v1/portal/tasks")
def api_tasks(principal: PortalPrincipal = Depends(current_portal)): return {"tasks": client_tasks(principal)}
@router.post("/api/v1/portal/tasks/{step_id}/complete", status_code=204)
def api_task_complete(step_id: int, principal: PortalPrincipal = Depends(current_portal)):
    try: complete_client_task(principal, step_id)
    except PermissionError as exc: raise HTTPException(403, str(exc))
@router.get("/api/v1/portal/notifications")
def api_notifications(principal: PortalPrincipal = Depends(current_portal)): return {"notifications": client_notifications(principal)}
@router.post("/api/v1/portal/notifications/{notification_id}/read", status_code=204)
def api_notification_read(notification_id: int, principal: PortalPrincipal = Depends(current_portal)):
    with engine.begin() as connection:
        changed = connection.execute(portal_notifications.update().where(portal_notifications.c.id == notification_id, portal_notifications.c.portal_account_id == principal.account_id).values(read_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc))).rowcount
    if not changed: raise HTTPException(404, "Notification not found")
