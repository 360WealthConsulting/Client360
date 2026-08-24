"""Email one existing Client360 document from the signed-in staff user's Microsoft 365 mailbox.

Two routes, both gated by ``communications.send`` (the capability the communications send surface
already uses). The attachment is fixed to the ``document_id`` in the path — a caller can never supply
a filesystem path, substitute a file, or rename the attachment. Document access is re-checked by the
service using the SAME authorization as the download route, so emailing can never widen access.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db import documents, engine, people
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.communications.mail_send import (
    MAX_ATTACHMENT_BYTES,
    DocumentNotAccessible,
    MailSendError,
    send_document_email,
)
from app.services.document_naming import document_delivery_filename
from app.templating import install_filters, render_error

router = APIRouter(tags=["documents"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


def _compose_context(principal, document_id):
    """Read-only view data for the compose form. Returns None when the document is not visible to
    this caller — the caller renders the same 404 either way, so existence is never disclosed."""
    from app.security.middleware import _document_in_scope
    with engine.connect() as conn:
        if not _document_in_scope(conn, principal, document_id, write=False):
            return None
        row = conn.execute(select(documents).where(documents.c.id == document_id)).mappings().first()
        if row is None or row["archived"] or row["status"] == "deleted":
            return None
        row = dict(row)
        # Prefill only from the owning person's canonical primary_email. Never guessed, never
        # inferred from a household or organization, and always editable by the staff user.
        prefill = None
        if row["person_id"]:
            prefill = conn.scalar(select(people.c.primary_email)
                                  .where(people.c.id == row["person_id"]))
    return {
        "document_id": row["id"],
        "attachment_filename": document_delivery_filename(row),
        "original_name": row["original_name"],
        "size_bytes": row["size_bytes"],
        "too_large": (row["size_bytes"] or 0) >= MAX_ATTACHMENT_BYTES,
        "max_bytes": MAX_ATTACHMENT_BYTES,
        "prefill_to": (prefill or "").strip() or None,
    }


@router.get("/documents/{document_id}/email", response_class=HTMLResponse)
def compose_document_email(
        request: Request, document_id: int,
        principal: Principal = Depends(require_capability("communications.send"))):
    """The compose form: To / Subject / Body, with the attachment fixed to this document."""
    ctx = _compose_context(principal, document_id)
    if ctx is None:
        return render_error(request, 404, detail="Document not found.")
    return templates.TemplateResponse(
        request=request, name="document_library/email.html",
        context={"principal": principal, "notice": request.query_params.get("msg"), **ctx})


@router.post("/documents/{document_id}/email", response_class=HTMLResponse)
def send_document_email_route(
        request: Request, document_id: int,
        to: str = Form(""), subject: str = Form(""), body: str = Form(""),
        principal: Principal = Depends(require_capability("communications.send"))):
    """Send it. The document is identified only by the path id; nothing about the file, its name or
    its location comes from the form."""
    try:
        result = send_document_email(principal=principal, document_id=document_id, to=to,
                                     subject=subject, body=body,
                                     request_id=request.state.request_id)
    except DocumentNotAccessible:
        return render_error(request, 404, detail="Document not found.")
    except MailSendError as exc:
        ctx = _compose_context(principal, document_id)
        if ctx is None:
            return render_error(request, 404, detail="Document not found.")
        return templates.TemplateResponse(
            request=request, name="document_library/email.html", status_code=400,
            context={"principal": principal, "error": str(exc), "to": to, "subject": subject,
                     "body": body, **ctx})
    ctx = _compose_context(principal, document_id) or {}
    return templates.TemplateResponse(
        request=request, name="document_library/email.html",
        context={"principal": principal, "sent": result, **ctx})
