"""Client Portal MVP API — /api/portal/*.

A thin, client-authenticated surface over the existing portal services + the Vault↔Portal bridge.
Auth is the existing portal session (resolved by the middleware, which now also forks on
``/api/portal``); routes depend on ``current_portal``. Login is invitation-based (MFA-ready) and
public. Documents integrate directly with the Vault: clients see only client-visible vault docs,
download approved ones, and upload documents that land pending employee approval.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.portal import profile as portal_profile
from app.portal import vault_documents as portal_vault
from app.portal.gate import gate as portal_runtime_gate
from app.portal.providers import PORTAL_IDENTITY_PROVIDERS
from app.portal.service import (
    PortalPrincipal,
    accept_invitation,
    client_document_requests,
    client_notifications,
    client_threads,
    create_portal_session,
    create_thread,
    dashboard,
    list_messages,
    portal_scope,
    send_message,
)
from app.routes.portal import current_portal
from app.services.vault.storage import VaultStorageError

router = APIRouter(prefix="/api/portal", tags=["portal-api"])


def _ip(request: Request):
    return request.client.host if request.client else None


def _rid(request: Request):
    return getattr(request.state, "request_id", "portal")


class PortalLogin(BaseModel):
    token: str
    identity_provider: str
    identity_assertion: str
    device_fingerprint: str
    device_name: str | None = None


class MessageBody(BaseModel):
    body: str
    subject: str | None = None
    thread_id: int | None = None


class ProfilePatch(BaseModel):
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    preferred_contact_method: str | None = None
    communication_preferences: dict | None = None


@router.post("/login")
def login(payload: PortalLogin, request: Request):
    """Invitation-based portal login (MFA-ready). Verifies the IdP activation assertion, accepts the
    invitation, and establishes a portal session. Public (no session yet)."""
    provider = PORTAL_IDENTITY_PROVIDERS.get(payload.identity_provider)
    if provider is None:
        raise HTTPException(400, "Unknown identity provider.")
    try:
        identity = provider.verify_activation(payload.identity_assertion)
        account_id = accept_invitation(payload.token, identity.subject, identity.mfa_verified)
        token = create_portal_session(
            account_id, device_fingerprint=payload.device_fingerprint, device_name=payload.device_name,
            ip_address=_ip(request), user_agent=request.headers.get("user-agent"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    request.session["portal_session_token"] = token
    return {"account_id": account_id, "mfa_verified": identity.mfa_verified}


@router.get("/dashboard")
def api_dashboard(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    data = dashboard(principal)
    scope = portal_scope(principal.account_id, permission="documents")
    return JSONResponse(_json({
        "welcome": principal.display_name,
        "meetings": data["meetings"], "requests": data["document_requests"],
        "documents": portal_vault.portal_documents(principal, scope),
        "messages": data["messages"], "notifications": data["notifications"],
        "tasks": data["tasks"],
    }))


@router.get("/documents")
def api_documents(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    """Only client-visible Vault documents linked to the portal account's persons."""
    return JSONResponse({"documents": portal_vault.portal_documents(principal)})


@router.post("/documents")
async def api_upload_document(
    request: Request,
    file: UploadFile = File(...),
    display_name: str = Form(...),
    category: str = Form("general"),
    document_type: str | None = Form(None),
    request_id: int | None = Form(None),
    principal: PortalPrincipal = Depends(current_portal),
):
    """Upload a document → stored as a PENDING vault document awaiting employee approval."""
    try:
        doc_id = portal_vault.upload_document(
            principal, source=file.file, original_filename=file.filename, display_name=display_name,
            category=category, document_type=document_type, request_id=request_id,
            http_request_id=_rid(request), ip_address=_ip(request))
    except (VaultStorageError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return JSONResponse({"id": doc_id, "status": "pending_approval"}, status_code=201)


@router.get("/documents/{document_id}/download")
def api_download_document(request: Request, document_id: int,
                          principal: PortalPrincipal = Depends(current_portal)):
    if not portal_runtime_gate("portal.documents.download_enabled"):
        # Feature unavailable — deliberately distinct from resource inaccessibility, and the same
        # answer the middleware gives before this route runs.
        raise HTTPException(403, "This feature is not available on your account.")
    try:
        path, filename, mime = portal_vault.download_document(
            principal, document_id, request_id=_rid(request), ip_address=_ip(request))
    except PermissionError as exc:
        # One generic 404 for every resource-level denial. Previously this returned 403 with the
        # service's message, which distinguished scope denial from gate denial and diverged from v1.
        raise HTTPException(404, "Document not found") from exc
    if not path.exists():
        raise HTTPException(404, "Document not found")
    return FileResponse(path=str(path), media_type=mime or "application/octet-stream", filename=filename)


@router.get("/messages")
def api_messages(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    return JSONResponse(_json({"threads": [dict(t) for t in client_threads(principal)]}))


@router.get("/messages/{thread_id}")
def api_message_thread(request: Request, thread_id: int,
                       principal: PortalPrincipal = Depends(current_portal)):
    try:
        messages = list_messages(principal, thread_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return JSONResponse(_json({"messages": [dict(m) for m in messages]}))


@router.post("/messages")
def api_send_message(request: Request, payload: MessageBody,
                     principal: PortalPrincipal = Depends(current_portal)):
    """Send a secure message: into an existing thread, or start a new one on the client's own record."""
    try:
        if payload.thread_id:
            message_id = send_message(principal, payload.thread_id, payload.body)
            return JSONResponse({"thread_id": payload.thread_id, "message_id": message_id}, status_code=201)
        scope = portal_scope(principal.account_id, permission="messages")
        household_id = next(iter(scope["household_ids"]), None)
        thread_id = create_thread(principal, household_id=household_id, person_id=principal.person_id,
                                  subject=payload.subject or "New message", body=payload.body)
        return JSONResponse({"thread_id": thread_id}, status_code=201)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/requests")
def api_requests(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    return JSONResponse(_json({"requests": [dict(r) for r in client_document_requests(principal)]}))


@router.post("/requests/{request_id}/upload")
async def api_request_upload(
    request: Request, request_id: int, file: UploadFile = File(...),
    display_name: str | None = Form(None), category: str = Form("general"),
    principal: PortalPrincipal = Depends(current_portal),
):
    """Fulfil a document request by uploading a file (pending employee approval)."""
    try:
        doc_id = portal_vault.upload_document(
            principal, source=file.file, original_filename=file.filename,
            display_name=display_name or file.filename, category=category, request_id=request_id,
            http_request_id=_rid(request), ip_address=_ip(request))
    except (VaultStorageError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return JSONResponse({"id": doc_id, "request_id": request_id, "status": "pending_approval"}, status_code=201)


@router.get("/profile")
def api_profile(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    return JSONResponse(_json(portal_profile.get_profile(principal)))


@router.patch("/profile")
def api_update_profile(request: Request, payload: ProfilePatch,
                       principal: PortalPrincipal = Depends(current_portal)):
    result = portal_profile.update_profile(
        principal, payload.model_dump(), request_id=_rid(request), ip_address=_ip(request))
    return JSONResponse(result)


@router.get("/notifications")
def api_notifications(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    return JSONResponse(_json({"notifications": [dict(n) for n in client_notifications(principal)]}))


def _json(value):
    """Best-effort JSON-safe conversion (datetimes → iso) for RowMapping payloads."""
    from datetime import date, datetime

    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
