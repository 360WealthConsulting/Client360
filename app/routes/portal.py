from typing import Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db import engine, portal_accounts, portal_document_requests, portal_notifications, portal_threads
from app.portal.service import (PortalPrincipal, accept_invitation, client_document_requests,
    client_documents, client_notifications, client_tasks, client_threads, complete_client_task,
    confirm_request_upload, create_portal_session, create_thread, dashboard,
    list_messages, mark_read, notify, request_password_reset, consume_password_reset,
    revoke_portal_session, send_message, require_scope)
from app.services.documents import save_person_document
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
