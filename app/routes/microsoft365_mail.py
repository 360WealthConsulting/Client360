"""The signed-in staff user's Outlook inbox, and READ-ONLY detail for one message.

The mailbox shown belongs to the AUTHENTICATED PRINCIPAL. This route used to read whichever
``microsoft_accounts`` row had been updated most recently, so with two connected mailboxes a user
holding ``communication.read`` could be shown a colleague's inbox. Account selection now goes
through the one canonical resolver (``microsoft_identity.account_for_principal``); there is no
fallback to another account.

The detail route is PREVIEW ONLY. It reads one message plus its attachment METADATA and shows who
the message may be about; it creates nothing, imports nothing, downloads no attachment bytes, and
exposes no mutation.
"""
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.forwarded_email import extract_candidate
from app.services.microsoft_identity import account_for_principal
from app.services.prospect_matching import match_for_candidate
from app.templating import render_error

router = APIRouter(prefix="/microsoft365")
templates = Jinja2Templates(directory="app/templates")

GRAPH_MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"

#: Everything the preview renders, and nothing else.
MESSAGE_SELECT = (
    "id,subject,from,sender,toRecipients,ccRecipients,receivedDateTime,"
    "body,bodyPreview,hasAttachments,conversationId,internetMessageId,webLink"
)
#: Attachment METADATA only. contentBytes is deliberately absent -- no attachment content is
#: fetched anywhere in this commit.
ATTACHMENT_SELECT = "id,name,contentType,size,isInline"


def _bearer(principal):
    """(token, redirect) for the principal's OWN mailbox. Exactly the list route's rules.

    Returns ``(None, RedirectResponse)`` for every not-connected case -- no account bound to this
    principal, an expired token, or no token at all -- so a caller can never proceed without one.
    """
    account = account_for_principal(principal)
    if account is None:
        return None, RedirectResponse(url="/microsoft365/connect", status_code=303)
    expires_at = account["expires_at"]
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        return None, RedirectResponse(url="/microsoft365/connect", status_code=303)
    token = account["access_token"]
    if not token:
        return None, RedirectResponse(url="/microsoft365/connect", status_code=303)
    return token, None


@router.get("/mail")
def microsoft365_mail(request: Request,
                      principal: Principal = Depends(require_capability("communication.read"))):
    # communication.read is the capability the middleware rule (^/microsoft) already applies to this
    # path; stating it here is what gives the route the principal it needs to pick the mailbox.
    # No connected account for THIS user -> the existing not-connected behaviour. Never another
    # user's mailbox, and never the most recently connected one.
    access_token, redirect = _bearer(principal)
    if redirect is not None:
        return redirect

    account = account_for_principal(principal)

    response = requests.get(
        GRAPH_MESSAGES_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        params={
            "$top": "25",
            "$select": (
                "id,subject,from,receivedDateTime,"
                "bodyPreview,isRead,hasAttachments,webLink"
            ),
            "$orderby": "receivedDateTime desc",
        },
        timeout=30,
    )

    if response.status_code == 401:
        return RedirectResponse(url="/microsoft365/connect", status_code=303)

    if not response.ok:
        return render_error(
            request, 500,
            detail=f"Outlook request failed (status {response.status_code}).",
        )

    messages = []
    for message in response.json().get("value", []):
        sender = message.get("from", {}).get("emailAddress", {})
        messages.append({
            "sender_name": sender.get("name") or "Unknown sender",
            "sender_address": sender.get("address") or "",
            "subject": message.get("subject") or "(No subject)",
            "received": message.get("receivedDateTime") or "",
            "preview": message.get("bodyPreview") or "",
            "web_link": message.get("webLink") or "#",
            # The subject now opens the in-app preview instead of leaving for Outlook. The id is an
            # opaque Graph identifier and is percent-encoded so it stays one path segment.
            "detail_url": f"/microsoft365/mail/{quote(message.get('id') or '', safe='')}",
            "is_read": bool(message.get("isRead")),
            "has_attachments": bool(message.get("hasAttachments")),
        })

    return templates.TemplateResponse(
        request=request,
        name="microsoft365/mail.html",
        context={"messages": messages, "account_email": account["email"] or ""},
    )


def _addresses(recipients):
    """['Name <addr>' ...] from a Graph recipient collection."""
    out = []
    for r in recipients or []:
        ea = (r or {}).get("emailAddress") or {}
        name, addr = (ea.get("name") or "").strip(), (ea.get("address") or "").strip()
        if name and addr and name.lower() != addr.lower():
            out.append(f"{name} <{addr}>")
        elif addr or name:
            out.append(addr or name)
    return out


def _attachment_rows(payload):
    """Attachment METADATA rows. Never reads contentBytes -- it is not even selected."""
    rows = []
    for a in (payload or {}).get("value", []):
        odata = a.get("@odata.type") or ""
        rows.append({
            "id": a.get("id"),
            "name": a.get("name") or "(unnamed)",
            "content_type": a.get("contentType") or "",
            "size": a.get("size") or 0,
            "is_inline": bool(a.get("isInline")),
            "odata_type": odata,
            # An attached .eml carries the ORIGINAL message and would be authoritative about the
            # prospect, unlike parsed body text. Reading it needs an $expand this commit does not
            # perform, so it is only flagged.
            "is_item_attachment": odata.endswith("itemAttachment"),
        })
    return rows


@router.get("/mail/{message_id}")
def microsoft365_mail_detail(
        request: Request, message_id: str,
        principal: Principal = Depends(require_capability("communication.read"))):
    """READ-ONLY preview of one message from the PRINCIPAL'S OWN mailbox.

    ``message_id`` is treated as an opaque Graph identifier: it is only ever passed to
    ``/me/messages/{id}`` on this principal's token. It never reaches a filesystem path, never
    selects a mailbox, and cannot address another user's message -- ``/me`` is resolved by the
    token, so an id belonging to someone else's mailbox simply 404s. A 404 or an invalid id gets the
    application's ordinary non-disclosing not-found response, identical either way, so the endpoint
    cannot be used to probe whether a message exists in another mailbox.

    Performs no writes of any kind: no person, opportunity, task, document, communication row or
    timeline event, and no attachment bytes.
    """
    access_token, redirect = _bearer(principal)
    if redirect is not None:
        return redirect

    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    # requests quotes the id into exactly one path segment; it is never concatenated into a URL by
    # hand and never used as a path on disk.
    message_url = f"{GRAPH_MESSAGES_URL}/{quote(message_id, safe='')}"
    response = requests.get(message_url, headers=headers,
                            params={"$select": MESSAGE_SELECT}, timeout=30)

    if response.status_code == 401:
        return RedirectResponse(url="/microsoft365/connect", status_code=303)
    if response.status_code in (400, 404):
        # Same response for "no such message" and "not in your mailbox" — existence is never
        # disclosed, matching how the rest of the app answers an out-of-scope record.
        return render_error(request, 404, detail="Message not found.")
    if not response.ok:
        return render_error(
            request, 500,
            detail=f"Outlook request failed (status {response.status_code}).")

    message = response.json()

    attachments = []
    if message.get("hasAttachments"):
        att = requests.get(f"{message_url}/attachments", headers=headers,
                           params={"$select": ATTACHMENT_SELECT}, timeout=30)
        # Attachment metadata is supporting detail; if it cannot be read the preview still renders.
        if att.ok:
            attachments = _attachment_rows(att.json())

    sender = (message.get("from") or message.get("sender") or {}).get("emailAddress") or {}
    body = message.get("body") or {}
    candidate = extract_candidate(
        body=body.get("content"),
        body_is_html=(body.get("contentType") or "html").lower() == "html",
        subject=message.get("subject"),
        # Passed as the FORWARDER. extract_candidate never promotes it to the prospect.
        graph_from_name=sender.get("name"),
        graph_from_email=sender.get("address"))

    # Only look for existing people once something was actually detected. With a blank candidate
    # this stays an empty result rather than a query that could echo unrelated client rows back.
    matches = ({"strategy": None, "matches": [], "outcome": "none"}
               if not (candidate["candidate_email"] or candidate["candidate_phone"]
                       or candidate["candidate_name"])
               else match_for_candidate(candidate))

    return templates.TemplateResponse(
        request=request,
        name="microsoft365/mail_detail.html",
        context={
            "subject": message.get("subject") or "(No subject)",
            "received": message.get("receivedDateTime") or "",
            "body_text": (message.get("bodyPreview") or "").strip(),
            "to": _addresses(message.get("toRecipients")),
            "cc": _addresses(message.get("ccRecipients")),
            "conversation_id": message.get("conversationId") or "",
            "internet_message_id": message.get("internetMessageId") or "",
            "web_link": message.get("webLink") or "#",
            "candidate": candidate,
            "matches": matches,
            "attachments": attachments,
            "has_item_attachment": any(a["is_item_attachment"] for a in attachments),
        },
    )
