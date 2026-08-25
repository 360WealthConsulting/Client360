"""The signed-in staff user's Outlook inbox.

The mailbox shown belongs to the AUTHENTICATED PRINCIPAL. This route used to read whichever
``microsoft_accounts`` row had been updated most recently, so with two connected mailboxes a user
holding ``communication.read`` could be shown a colleague's inbox. Account selection now goes
through the one canonical resolver (``microsoft_identity.account_for_principal``); there is no
fallback to another account.
"""
from datetime import datetime, timezone

import requests
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.microsoft_identity import account_for_principal
from app.templating import render_error

router = APIRouter(prefix="/microsoft365")
templates = Jinja2Templates(directory="app/templates")


@router.get("/mail")
def microsoft365_mail(request: Request,
                      principal: Principal = Depends(require_capability("communication.read"))):
    # communication.read is the capability the middleware rule (^/microsoft) already applies to this
    # path; stating it here is what gives the route the principal it needs to pick the mailbox.
    account = account_for_principal(principal)

    # No connected account for THIS user -> the existing not-connected behaviour. Never another
    # user's mailbox, and never the most recently connected one.
    if account is None:
        return RedirectResponse(url="/microsoft365/connect", status_code=303)

    expires_at = account["expires_at"]
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        return RedirectResponse(url="/microsoft365/connect", status_code=303)

    access_token = account["access_token"]
    if not access_token:
        return RedirectResponse(url="/microsoft365/connect", status_code=303)

    response = requests.get(
        "https://graph.microsoft.com/v1.0/me/messages",
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
            "is_read": bool(message.get("isRead")),
            "has_attachments": bool(message.get("hasAttachments")),
        })

    return templates.TemplateResponse(
        request=request,
        name="microsoft365/mail.html",
        context={"messages": messages, "account_email": account["email"] or ""},
    )
