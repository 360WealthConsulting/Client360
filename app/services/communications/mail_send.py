"""Email ONE existing Client360 document from the signed-in staff user's Microsoft 365 mailbox.

Delegated Microsoft Graph ``/me/sendMail`` with a single inline attachment. Send-as-self only: the
token is the staff user's own, so the message comes from their mailbox and lands in their Sent Items
exactly as if they had sent it from Outlook. There is no shared mailbox, no application-wide
Mail.Send, no SMTP, and no second provider abstraction.

What this module does NOT do: it never renames, moves, reclassifies or writes a document; it never
accepts a filesystem path from the caller; and it never reads anything but the canonical bytes the
download route would have served. The attachment is named by the same
``document_naming.document_delivery_filename`` the download uses, so what a client receives by email
matches what staff see on screen and what they would get by downloading.

Attachment size is capped strictly BELOW 3 MB. At or above that the send is refused before any Graph
call — Graph's inline-attachment limit needs an upload session, which is deliberately out of scope.
"""
from __future__ import annotations

import base64
import mimetypes
import re
import uuid
from pathlib import Path

from sqlalchemy import select

from app.db import documents, engine
from app.security.audit import write_audit_event
from app.security.middleware import _document_in_scope
from app.services.document_naming import document_delivery_filename
from app.services.microsoft_identity import account_for_principal, get_microsoft_access_token

GRAPH_SENDMAIL_URL = "https://graph.microsoft.com/v1.0/me/sendMail"

#: Strictly BELOW 3 MB. Graph rejects larger inline attachments; upload sessions are out of scope.
MAX_ATTACHMENT_BYTES = 3 * 1024 * 1024

_REQUIRED_CAPABILITY = "communications.send"
#: Same wording whether the document is missing or out of scope — existence is never disclosed.
_NOT_ACCESSIBLE = "Document not found."
_TOO_LARGE = ("This file is too large to email from Client360 (limit 3 MB). "
              "Download it and attach it in Outlook instead.")
_NOT_CONNECTED = ("Microsoft 365 is not connected for your account. Connect it before emailing "
                  "documents.")
# Deliberately strict and simple: one address, no display-name form, no CR/LF (header injection).
_EMAIL_RE = re.compile(r"^[^@\s,;<>\"]+@[^@\s,;<>\"]+\.[A-Za-z]{2,}$")


class MailSendError(RuntimeError):
    """A user-facing refusal — safe to show a staff user verbatim."""


class DocumentNotAccessible(MailSendError):
    """The document does not exist OR is out of the caller's scope. Never distinguishes the two."""


def _rid(request_id):
    return request_id or f"mailsend-{uuid.uuid4()}"


def validate_recipient(to: str | None) -> str:
    address = (to or "").strip()
    if not address or "\r" in address or "\n" in address or not _EMAIL_RE.match(address):
        raise MailSendError("Enter one valid recipient email address.")
    return address


def _content_type(document) -> str:
    """The same convention the download route uses: the recorded content_type, else guessed from the
    ORIGINAL filename's extension, else a safe binary default. Never guessed from the display name."""
    recorded = (document["content_type"] or "").strip()
    if recorded:
        return recorded
    guessed, _ = mimetypes.guess_type(document["original_name"] or "")
    return guessed or "application/octet-stream"


def _stored_path(document) -> Path:
    """Resolve the canonical stored file exactly as ``routes.documents.download_document`` does.
    The path comes only from the document row — never from a request."""
    uri = document["storage_uri"]
    return Path(uri) if uri and Path(uri).is_absolute() else Path(document["storage_path"])


def _load_authorized_document(principal, document_id):
    """The document row, only if the caller could also download it. Same effective authorization as
    the download route (``_document_in_scope``), so email can never widen document access."""
    if not isinstance(document_id, int) or isinstance(document_id, bool) or document_id <= 0:
        raise DocumentNotAccessible(_NOT_ACCESSIBLE)
    with engine.connect() as conn:
        if not _document_in_scope(conn, principal, document_id, write=False):
            raise DocumentNotAccessible(_NOT_ACCESSIBLE)
        row = conn.execute(select(documents).where(documents.c.id == document_id)).mappings().first()
    if row is None or row["archived"] or row["status"] == "deleted":
        raise DocumentNotAccessible(_NOT_ACCESSIBLE)
    return dict(row)


def _staff_microsoft_account(principal):
    """The connected Microsoft account belonging to THIS staff user, matched on their sign-in email.

    Matching on the principal's own address is what keeps delegated send honest: without it a staff
    user could send from whichever mailbox happened to be connected last. No match means no send.

    Delegates to ``microsoft_identity.account_for_principal`` -- the one resolver the mail READ
    surface now uses too -- so send and read can never drift onto different mailboxes. Same
    case-insensitive rule as before; the resolver compares ``lower(email)`` instead of ``ILIKE`` so
    an underscore in a local part is a literal rather than a wildcard.
    """
    account = account_for_principal(principal)
    if account is None:
        raise MailSendError(_NOT_CONNECTED)
    return account


def _post_to_graph(token: str, payload: dict) -> object:
    """Real Graph transport. Injected in tests — no network call is made there."""
    import requests
    return requests.post(GRAPH_SENDMAIL_URL, json=payload, timeout=30,
                         headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": "application/json"})


def send_document_email(*, principal, document_id, to, subject, body,
                        graph_post=None, request_id=None) -> dict:
    """Email one canonical document as an attachment. Returns a structured result on success.

    Raises ``PermissionError`` without ``communications.send``, ``DocumentNotAccessible`` when the
    document is missing or out of scope (indistinguishable by design), and ``MailSendError`` for an
    invalid recipient, an oversized file, a missing Microsoft connection, or a Graph failure.
    """
    if not principal.can(_REQUIRED_CAPABILITY):
        raise PermissionError(f"Missing capability: {_REQUIRED_CAPABILITY}")
    recipient = validate_recipient(to)
    document = _load_authorized_document(principal, document_id)

    path = _stored_path(document)
    if not path.is_file():
        raise MailSendError("The stored copy of this document could not be found on the server.")
    # Size is checked BEFORE the mailbox lookup and before any Graph call, so an oversized document
    # never reaches the network.
    size = path.stat().st_size
    if size >= MAX_ATTACHMENT_BYTES:
        _audit(principal, document, recipient, None, "refused_too_large", request_id, size=size)
        raise MailSendError(_TOO_LARGE)

    filename = document_delivery_filename(document)
    account = _staff_microsoft_account(principal)
    token = get_microsoft_access_token(account)

    payload = {
        "message": {
            "subject": (subject or "").strip(),
            "body": {"contentType": "Text", "content": body or ""},
            "toRecipients": [{"emailAddress": {"address": recipient}}],
            "attachments": [{
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": filename,
                "contentType": _content_type(document),
                "contentBytes": base64.b64encode(path.read_bytes()).decode("ascii"),
            }],
        },
        "saveToSentItems": True,
    }

    post = graph_post or _post_to_graph
    try:
        response = post(token, payload)
    except Exception as exc:                                  # noqa: BLE001 - reported, never swallowed
        _audit(principal, document, recipient, filename, "failed", request_id)
        raise MailSendError("Microsoft 365 could not send this message. Try again.") from exc
    status = getattr(response, "status_code", None)
    if status not in (200, 202):                              # Graph returns 202 Accepted on success
        _audit(principal, document, recipient, filename, "failed", request_id, status=status)
        raise MailSendError("Microsoft 365 rejected this message. Try again.")

    provider_ref = (getattr(response, "headers", None) or {}).get("request-id")
    _audit(principal, document, recipient, filename, "sent", request_id, provider_ref=provider_ref)
    return {"ok": True, "document_id": document["id"], "recipient": recipient,
            "attachment_filename": filename, "attachment_bytes": size,
            "provider": "microsoft_graph", "provider_ref": provider_ref,
            "from_mailbox": account["email"]}


def _audit(principal, document, recipient, filename, outcome, request_id, **extra):
    """Existing platform audit helper — no new auditing subsystem. Never records bytes, tokens or
    filesystem paths."""
    write_audit_event(action=f"document.emailed.{outcome}", entity_type="document",
                      entity_id=document["id"], actor_user_id=principal.user_id,
                      request_id=_rid(request_id),
                      metadata={"recipient": recipient, "attachment_filename": filename,
                                "provider": "microsoft_graph", "outcome": outcome, **extra})
