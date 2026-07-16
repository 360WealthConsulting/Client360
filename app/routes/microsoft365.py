from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.connectors.microsoft365.config import (
    get_microsoft365_config,
)
from app.db import engine, microsoft_accounts


router = APIRouter(prefix="/microsoft365")
templates = Jinja2Templates(directory="app/templates")


def _sync_health():
    """Microsoft 365 sync-health summary (0.9.9 monitoring), or None if no account."""
    with engine.connect() as connection:
        account = connection.execute(
            select(microsoft_accounts.c.email, microsoft_accounts.c.token_cache_encrypted,
                   microsoft_accounts.c.last_sync_at, microsoft_accounts.c.last_sync_status,
                   microsoft_accounts.c.last_sync_error)
            .order_by(microsoft_accounts.c.updated_at.desc()).limit(1)
        ).mappings().one_or_none()
    if account is None:
        return None
    return {
        "email": account["email"] or "",
        "connected": ("Connected (refreshable token stored)"
                      if account["token_cache_encrypted"]
                      else "Not connected — reconnect required"),
        "last_sync_at": str(account["last_sync_at"] or "never"),
        "last_sync_status": str(account["last_sync_status"] or "unknown"),
        "last_sync_error": str(account["last_sync_error"] or "none"),
    }


@router.get("/status")
def microsoft365_status(request: Request):
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

    return templates.TemplateResponse(
        request=request,
        name="microsoft365/status.html",
        context={
            "configured": configured,
            "message": message,
            "status_label": "Configured" if configured else "Not Configured",
            "tenant_id": tenant_id,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "sync": _sync_health(),
        },
    )
