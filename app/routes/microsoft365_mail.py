from datetime import datetime, timezone
from html import escape

import requests
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.db import engine, microsoft_accounts


router = APIRouter(prefix="/microsoft365")


@router.get("/mail", response_class=HTMLResponse)
def microsoft365_mail():
    with engine.connect() as connection:
        account = connection.execute(
            select(microsoft_accounts)
            .order_by(microsoft_accounts.c.updated_at.desc())
            .limit(1)
        ).mappings().one_or_none()

    if account is None:
        return RedirectResponse(
            url="/microsoft365/connect",
            status_code=303,
        )

    expires_at = account["expires_at"]

    if (
        expires_at is not None
        and expires_at <= datetime.now(timezone.utc)
    ):
        return RedirectResponse(
            url="/microsoft365/connect",
            status_code=303,
        )

    access_token = account["access_token"]

    if not access_token:
        return RedirectResponse(
            url="/microsoft365/connect",
            status_code=303,
        )

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
        return RedirectResponse(
            url="/microsoft365/connect",
            status_code=303,
        )

    if not response.ok:
        return HTMLResponse(
            f"""
            <h1>Outlook request failed</h1>
            <p>Status: {response.status_code}</p>
            <pre>{escape(response.text)}</pre>
            """,
            status_code=response.status_code,
        )

    messages = response.json().get("value", [])
    message_cards = ""

    for message in messages:
        sender = (
            message.get("from", {})
            .get("emailAddress", {})
        )

        sender_name = sender.get("name") or "Unknown sender"
        sender_address = sender.get("address") or ""
        subject = message.get("subject") or "(No subject)"
        received = message.get("receivedDateTime") or ""
        preview = message.get("bodyPreview") or ""
        web_link = message.get("webLink") or "#"
        is_read = bool(message.get("isRead"))
        has_attachments = bool(message.get("hasAttachments"))

        status = "Read" if is_read else "Unread"
        attachment_text = (
            " • Has attachments"
            if has_attachments
            else ""
        )

        message_cards += f"""
        <div class="message {'read' if is_read else 'unread'}">
            <div class="message-status">
                {escape(status)}{attachment_text}
            </div>

            <h2>
                <a
                    href="{escape(web_link)}"
                    target="_blank"
                    rel="noopener noreferrer"
                >
                    {escape(subject)}
                </a>
            </h2>

            <p class="sender">
                {escape(sender_name)}
                &lt;{escape(sender_address)}&gt;
            </p>

            <p class="received">
                {escape(received)}
            </p>

            <p>{escape(preview)}</p>
        </div>
        """

    if not message_cards:
        message_cards = """
        <div class="message">
            <p>No Outlook messages were returned.</p>
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta
            name="viewport"
            content="width=device-width, initial-scale=1.0"
        >
        <title>Outlook Mail - Client360</title>

        <style>
            body {{
                margin: 0;
                font-family: Arial, sans-serif;
                background: #f3f4f6;
                color: #1f2937;
            }}

            header {{
                background: #111827;
                color: white;
                padding: 28px 40px;
            }}

            main {{
                max-width: 1100px;
                padding: 32px 40px;
            }}

            nav {{
                display: flex;
                gap: 18px;
                margin-bottom: 24px;
            }}

            a {{
                color: #2563eb;
                text-decoration: none;
                font-weight: bold;
            }}

            .message {{
                background: white;
                padding: 22px;
                margin-bottom: 16px;
                border-radius: 10px;
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
            }}

            .unread {{
                border-left: 5px solid #2563eb;
            }}

            .read {{
                opacity: 0.82;
            }}

            .message-status,
            .received {{
                color: #6b7280;
                font-size: 14px;
            }}

            .sender {{
                font-weight: bold;
            }}
        </style>
    </head>

    <body>
        <header>
            <h1>Outlook Mail</h1>
            <p>
                Latest messages for
                {escape(account["email"] or "")}
            </p>
        </header>

        <main>
            <nav>
                <a href="/">Dashboard</a>
                <a href="/microsoft365/profile">
                    Microsoft Profile
                </a>
                <a href="/microsoft365/status">
                    Connection Status
                </a>
            </nav>

            <h2>{len(messages)} Recent Messages</h2>

            {message_cards}
        </main>
    </body>
    </html>
    """
