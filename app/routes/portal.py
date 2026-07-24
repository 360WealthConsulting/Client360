from typing import Optional
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db import documents, engine, portal_document_requests, portal_notifications, portal_sessions
from app.portal.service import (PortalPrincipal, accept_invitation, client_action_detail,
    client_action_needed, client_document_requests,
    client_documents, client_notifications, client_tasks, client_threads, complete_client_task,
    confirm_request_upload, create_portal_session, create_thread, dashboard,
    employer_action_detail, employer_action_needed, employer_census_upload, employer_organization_ids,
    list_messages, mark_read, request_password_reset, consume_password_reset,
    revoke_portal_session, send_message, require_scope)
from app.portal import appointments as portal_appointments
from app.portal import consent as portal_consent
from app.portal.financial import financial_summary
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

@router.get("/portal/login", response_class=HTMLResponse)
def portal_login(request: Request): return templates.TemplateResponse(request=request, name="portal/login.html", context={})

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
        raise HTTPException(404, "Organization not found")
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
def api_portal_document_download(document_id: int, principal: PortalPrincipal = Depends(current_portal)):
    # File-security: resolve the document, enforce person scope under the documents grant, and stream from
    # the authoritative document store only after the scope check. Out-of-scope never discloses existence.
    with engine.connect() as connection:
        row = connection.execute(select(documents.c.person_id, documents.c.storage_path,
            documents.c.original_name, documents.c.content_type, documents.c.archived).where(
            documents.c.id == document_id)).mappings().one_or_none()
    if not row or row["archived"]:
        raise HTTPException(404, "Document not found")
    try:
        require_scope(principal, person_id=row["person_id"], permission="documents")
    except PermissionError as exc:
        raise HTTPException(404, "Document not found") from exc  # scope denial does not reveal existence
    from pathlib import Path

    from fastapi.responses import FileResponse
    path = Path(row["storage_path"])
    if not path.is_file():
        raise HTTPException(404, "Document not found")
    from app.portal import stats as _stats
    _stats.note("downloads")
    return FileResponse(str(path), media_type=row["content_type"] or "application/octet-stream",
                        filename=row["original_name"])


PAGE_NAMES = {"": "dashboard", "messages": "messages", "documents": "documents", "requests": "requests", "tasks": "tasks", "notifications": "notifications", "settings": "settings"}
@router.get("/portal/{page:path}", response_class=HTMLResponse)
def portal_page(page: str, request: Request, principal: PortalPrincipal = Depends(current_portal)):
    if page not in PAGE_NAMES: raise HTTPException(404, "Portal page not found")
    data = dashboard(principal)
    return templates.TemplateResponse(request=request, name=f"portal/{PAGE_NAMES[page]}.html", context={"portal": data, "principal": principal})

@router.get("/api/v1/portal/dashboard")
def api_dashboard(principal: PortalPrincipal = Depends(current_portal)): return dashboard(principal)
@router.get("/api/v1/portal/profile")
def api_profile(principal: PortalPrincipal = Depends(current_portal)): return principal.__dict__
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
    with engine.connect() as connection: row = connection.execute(select(portal_document_requests).where(portal_document_requests.c.id == request_id)).mappings().one_or_none()
    if not row: raise HTTPException(404, "Document request not found")
    try: require_scope(principal, person_id=row["person_id"], household_id=row["household_id"], permission="documents")
    except PermissionError as exc: raise HTTPException(403, str(exc))
    document_id = save_person_document(person_id=row["person_id"], original_name=file.filename or "portal-upload", source=file.file, content_type=file.content_type, category="portal_request", description=row["title"], uploaded_by=principal.display_name)
    await file.close()
    try: version = confirm_request_upload(principal, request_id, document_id)
    except PermissionError as exc: raise HTTPException(403, str(exc))
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
