from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.connectors.microsoft365.config import (
    get_microsoft365_config,
)
from app.db import engine, microsoft_accounts


router = APIRouter(prefix="/microsoft365")


def _sync_health_html() -> str:
    """Render the Microsoft 365 sync-health summary (0.9.9 monitoring)."""
    with engine.connect() as connection:
        account = connection.execute(
            select(microsoft_accounts.c.email, microsoft_accounts.c.token_cache_encrypted,
                   microsoft_accounts.c.last_sync_at, microsoft_accounts.c.last_sync_status,
                   microsoft_accounts.c.last_sync_error)
            .order_by(microsoft_accounts.c.updated_at.desc()).limit(1)
        ).mappings().one_or_none()
    if account is None:
        return "<dl><dt>Sync health</dt><dd>No Microsoft 365 account is connected.</dd></dl>"
    connected = "Connected (refreshable token stored)" if account["token_cache_encrypted"] else "Not connected — reconnect required"
    return (
        "<dl>"
        f"<dt>Connected account</dt><dd>{escape(account['email'] or '')}</dd>"
        f"<dt>Token status</dt><dd>{escape(connected)}</dd>"
        f"<dt>Last sync</dt><dd>{escape(str(account['last_sync_at'] or 'never'))}</dd>"
        f"<dt>Last sync status</dt><dd>{escape(str(account['last_sync_status'] or 'unknown'))}</dd>"
        f"<dt>Last sync error</dt><dd>{escape(str(account['last_sync_error'] or 'none'))}</dd>"
        "</dl>"
    )


@router.get("/status", response_class=HTMLResponse)
def microsoft365_status():
    try:
        config = get_microsoft365_config()
        configured = True
        message = "Microsoft 365 configuration is present."
        tenant_id = config.tenant_id
        client_id = config.client_id
        redirect_uri = config.redirect_uri
    except RuntimeError as exc:
        configured = False
        message = str(exc)
        tenant_id = "Not configured"
        client_id = "Not configured"
        redirect_uri = (
            "http://localhost:8000/microsoft365/callback"
        )

    status_label = "Configured" if configured else "Not Configured"
    status_class = "success" if configured else "warning"
    sync_health = _sync_health_html()

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta
            name="viewport"
            content="width=device-width, initial-scale=1.0"
        >
        <title>Microsoft 365 Status - Client360</title>

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
                max-width: 900px;
                padding: 32px 40px;
            }}

            a {{
                color: #2563eb;
                text-decoration: none;
                font-weight: bold;
            }}

            .card {{
                background: white;
                padding: 22px;
                border-radius: 10px;
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
            }}

            .success {{
                color: #166534;
                background: #dcfce7;
                padding: 12px;
                border-left: 4px solid #15803d;
            }}

            .warning {{
                color: #92400e;
                background: #fef3c7;
                padding: 12px;
                border-left: 4px solid #d97706;
            }}

            dt {{
                margin-top: 16px;
                font-weight: bold;
            }}

            dd {{
                margin-left: 0;
                margin-top: 4px;
            }}
        </style>
    </head>

    <body>
        <header>
            <h1>Microsoft 365 Integration</h1>
            <p>Client360 connector status</p>
        </header>

        <main>
            <p><a href="/">← Back to dashboard</a></p>

            <div class="card">
                <div class="{status_class}">
                    <strong>{status_label}</strong><br>
                    {message}
                </div>

                <dl>
                    <dt>Tenant ID</dt>
                    <dd>{tenant_id}</dd>

                    <dt>Client ID</dt>
                    <dd>{client_id}</dd>

                    <dt>Redirect URI</dt>
                    <dd>{redirect_uri}</dd>
                </dl>

                <p>
                    Client secrets are never displayed on this page.
                </p>
            </div>

            <div class="card" style="margin-top: 20px;">
                <h2>Sync health</h2>
                {sync_health}
            </div>
        </main>
    </body>
    </html>
    """
