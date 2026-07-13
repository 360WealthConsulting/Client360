from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Dict

import msal
import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.connectors.microsoft365.config import get_microsoft365_config
from app.db import engine, microsoft_accounts


router = APIRouter(prefix="/microsoft365")

DELEGATED_SCOPES = [
    "User.Read",
    "Mail.Read",
    "Mail.Send",
    "Calendars.ReadWrite",
    "Contacts.ReadWrite",
    "Files.ReadWrite",
    "Sites.Read.All",
]


def build_msal_client() -> msal.ConfidentialClientApplication:
    config = get_microsoft365_config()

    return msal.ConfidentialClientApplication(
        client_id=config.client_id,
        authority=config.authority,
        client_credential=config.client_secret,
    )


def error_page(
    title: str,
    details: str,
    status_code: int = 400,
) -> HTMLResponse:
    return HTMLResponse(
        f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>{escape(title)}</title>
        </head>
        <body style="font-family: Arial; margin: 40px;">
            <h1>{escape(title)}</h1>
            <p>{escape(details)}</p>
            <p>
                <a href="/microsoft365/status">
                    Return to Microsoft 365 status
                </a>
            </p>
        </body>
        </html>
        """,
        status_code=status_code,
    )


@router.get("/connect")
def connect_microsoft365(request: Request):
    config = get_microsoft365_config()
    client = build_msal_client()

    flow = client.initiate_auth_code_flow(
        scopes=DELEGATED_SCOPES,
        redirect_uri=config.redirect_uri,
    )

    if "auth_uri" not in flow:
        return error_page(
            "Unable to start Microsoft sign-in",
            str(flow),
            status_code=500,
        )

    request.session["microsoft_auth_flow"] = flow

    return RedirectResponse(
        url=flow["auth_uri"],
        status_code=302,
    )


@router.get("/callback")
def microsoft365_callback(request: Request):
    flow = request.session.get("microsoft_auth_flow")

    if not flow:
        return error_page(
            "Microsoft sign-in session expired",
            "Return to the status page and start the connection again.",
        )

    client = build_msal_client()

    try:
        result: Dict[str, Any] = (
            client.acquire_token_by_auth_code_flow(
                flow,
                dict(request.query_params),
            )
        )
    except ValueError:
        return error_page(
            "Microsoft sign-in validation failed",
            "The authorization response could not be validated.",
        )

    request.session.pop("microsoft_auth_flow", None)

    access_token = result.get("access_token")

    if not access_token:
        return error_page(
            "Microsoft authentication failed",
            result.get(
                "error_description",
                result.get("error", "No access token was returned."),
            ),
        )

    graph_response = requests.get(
        "https://graph.microsoft.com/v1.0/me",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        params={
            "$select": (
                "id,displayName,mail,userPrincipalName,"
                "givenName,surname"
            )
        },
        timeout=30,
    )

    if not graph_response.ok:
        return error_page(
            "Microsoft Graph profile request failed",
            graph_response.text,
            status_code=graph_response.status_code,
        )

    profile = graph_response.json()
    claims = result.get("id_token_claims", {})

    tenant_id = (
        claims.get("tid")
        or get_microsoft365_config().tenant_id
    )
    user_id = profile.get("id") or claims.get("oid")
    email = (
        profile.get("mail")
        or profile.get("userPrincipalName")
        or claims.get("preferred_username")
    )
    display_name = (
        profile.get("displayName")
        or claims.get("name")
        or email
    )

    if not user_id or not email:
        return error_page(
            "Microsoft profile is incomplete",
            "Microsoft did not return a user ID and email address.",
        )

    expires_in = int(result.get("expires_in", 3600))
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=expires_in
    )

    statement = (
        pg_insert(microsoft_accounts)
        .values(
            tenant_id=tenant_id,
            user_id=user_id,
            email=email,
            display_name=display_name,
            access_token=access_token,
            refresh_token=None,
            expires_at=expires_at,
        )
        .on_conflict_do_update(
            constraint="uq_microsoft_account",
            set_={
                "email": email,
                "display_name": display_name,
                "access_token": access_token,
                "expires_at": expires_at,
                "updated_at": datetime.now(timezone.utc),
            },
        )
    )

    with engine.begin() as connection:
        connection.execute(statement)

    request.session["microsoft_user"] = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "email": email,
        "display_name": display_name,
    }

    return RedirectResponse(
        url="/microsoft365/profile",
        status_code=303,
    )


@router.get("/profile", response_class=HTMLResponse)
def microsoft365_profile(request: Request):
    session_user = request.session.get("microsoft_user")

    with engine.connect() as connection:
        if session_user:
            account = connection.execute(
                select(microsoft_accounts).where(
                    microsoft_accounts.c.tenant_id
                    == session_user["tenant_id"],
                    microsoft_accounts.c.user_id
                    == session_user["user_id"],
                )
            ).mappings().one_or_none()
        else:
            account = connection.execute(
                select(microsoft_accounts)
                .order_by(
                    microsoft_accounts.c.updated_at.desc()
                )
                .limit(1)
            ).mappings().one_or_none()

    if account is None:
        return RedirectResponse(
            url="/microsoft365/status",
            status_code=303,
        )

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Microsoft 365 Profile - Client360</title>
    </head>
    <body style="font-family: Arial; margin: 40px;">
        <h1>Microsoft 365 authentication succeeded</h1>

        <p><strong>Name:</strong>
            {escape(account["display_name"] or "")}
        </p>

        <p><strong>Email:</strong>
            {escape(account["email"] or "")}
        </p>

        <p><strong>Microsoft User ID:</strong>
            {escape(account["user_id"] or "")}
        </p>

        <p><strong>Tenant ID:</strong>
            {escape(account["tenant_id"] or "")}
        </p>

        <p><strong>Token Expires:</strong>
            {escape(str(account["expires_at"] or ""))}
        </p>

        <form method="post" action="/microsoft365/disconnect">
            <button type="submit">
                Disconnect Microsoft 365
            </button>
        </form>
    </body>
    </html>
    """


@router.post("/disconnect")
def disconnect_microsoft365(request: Request):
    session_user = request.session.get("microsoft_user")

    if session_user:
        with engine.begin() as connection:
            connection.execute(
                delete(microsoft_accounts).where(
                    microsoft_accounts.c.tenant_id
                    == session_user["tenant_id"],
                    microsoft_accounts.c.user_id
                    == session_user["user_id"],
                )
            )

    request.session.pop("microsoft_user", None)
    request.session.pop("microsoft_auth_flow", None)

    return RedirectResponse(
        url="/microsoft365/status",
        status_code=303,
    )
