from datetime import datetime, timezone

import requests
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db import engine, microsoft_accounts
from app.templating import render_error


router = APIRouter(prefix="/microsoft365")
templates = Jinja2Templates(directory="app/templates")


@router.get("/mail")
def microsoft365_mail(request: Request):
    with engine.connect() as connection:
        account = connection.execute(
            select(microsoft_accounts)
            .order_by(microsoft_accounts.c.updated_at.desc())
            .limit(1)
        ).mappings().one_or_none()

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
