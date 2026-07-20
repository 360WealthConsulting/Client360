"""Development-only authentication provider (NEVER production).

Lets a developer — or Playwright — sign in as a deterministic staff persona
*without* an external identity provider, by issuing a session through the SAME
``authenticate_claims`` + ``create_session`` path the real OIDC callback uses. It
is not an authorization bypass: the persona gets exactly the capabilities of its
seeded role, and every sign-in is audited.

Production activation is impossible by construction, guarded twice:

1. ``app.main`` mounts this router only when :func:`dev_auth_enabled` is True, and
   that function returns False whenever ``CLIENT360_ENVIRONMENT`` is ``production``
   — regardless of the ``CLIENT360_DEV_AUTH`` toggle.
2. Every handler re-asserts :func:`_guard`, so even a mis-mounted router refuses to
   act when dev auth is not enabled.

Enable locally / in CI with::

    CLIENT360_ENVIRONMENT=development CLIENT360_DEV_AUTH=1

The deterministic personas come from ``app.demo.credentials`` (already used by the
demo app), and the matching user + role are provisioned on demand so a freshly
migrated database needs no separate seed step.
"""
from __future__ import annotations

import os
from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.db import engine, roles, user_roles, users
from app.demo.credentials import DEMO_STAFF, staff_by_username
from app.integrations.identity.base import IdentityClaims
from app.security.audit import write_audit_event
from app.security.service import authenticate_claims, create_session, normalize_email

DEV_AUTH_ENV = "CLIENT360_DEV_AUTH"
DEV_AUTH_PREFIX = "/dev-auth"

router = APIRouter(prefix=DEV_AUTH_PREFIX, tags=["dev-auth"])


def _is_production() -> bool:
    return os.getenv("CLIENT360_ENVIRONMENT", "development").strip().lower() == "production"


def dev_auth_enabled() -> bool:
    """True only in a non-production environment with the toggle explicitly set.

    The production check wins unconditionally — dev auth can never be enabled in
    production, even if the toggle is present.
    """
    if _is_production():
        return False
    return os.getenv(DEV_AUTH_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _guard() -> None:
    # Defence in depth: behave as if the routes do not exist unless dev auth is on.
    if not dev_auth_enabled():
        raise HTTPException(status_code=404, detail="Not found")


def _ensure_dev_user(persona) -> int:
    """Idempotently provision the deterministic persona's active user + role. Returns user id."""
    with engine.begin() as connection:
        row = connection.execute(
            select(users.c.id).where(users.c.auth_subject == persona.auth_subject)
        ).first()
        if row is None:
            row = connection.execute(
                select(users.c.id).where(users.c.normalized_email == normalize_email(persona.email))
            ).first()
        if row is None:
            user_id = connection.execute(
                users.insert().values(
                    email=persona.email, normalized_email=normalize_email(persona.email),
                    display_name=persona.display_name, status="active",
                    auth_subject=persona.auth_subject,
                ).returning(users.c.id)
            ).scalar_one()
        else:
            user_id = row[0]
            connection.execute(users.update().where(users.c.id == user_id).values(
                status="active", auth_subject=persona.auth_subject))

        role_id = connection.execute(
            select(roles.c.id).where(roles.c.code == persona.role_code)
        ).scalar_one_or_none()
        if role_id is not None:
            already = connection.execute(select(user_roles.c.id).where(
                user_roles.c.user_id == user_id, user_roles.c.role_id == role_id,
                user_roles.c.inactive_date.is_(None))).first()
            if already is None:
                connection.execute(user_roles.insert().values(user_id=user_id, role_id=role_id))
    return user_id


def _page() -> str:
    buttons = "".join(
        f'<li><form method="post" action="{DEV_AUTH_PREFIX}/login/{escape(u.username)}">'
        f'<button type="submit" data-persona="{escape(u.username)}">{escape(u.persona)} '
        f'({escape(u.username)})</button></form></li>'
        for u in DEMO_STAFF
    )
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        "<title>Client360 — Developer sign-in</title></head><body>"
        "<h1>Client360 developer sign-in</h1>"
        "<p><strong>Development only.</strong> This provider is disabled in production. "
        "Each persona signs in through the real session path (no external IdP) with its "
        "seeded role's capabilities.</p>"
        f"<ul>{buttons}</ul></body></html>"
    )


@router.get("/login", response_class=HTMLResponse)
def dev_login_page(request: Request) -> HTMLResponse:
    _guard()
    return HTMLResponse(_page())


@router.post("/login/{username}")
def dev_login_as(request: Request, username: str):
    _guard()
    persona = staff_by_username(username)
    if persona is None:
        raise HTTPException(status_code=404, detail="Unknown dev persona")
    _ensure_dev_user(persona)
    claims = IdentityClaims(
        subject=persona.auth_subject, email=persona.email,
        display_name=persona.display_name, mfa_authenticated=True,
    )
    user_id = authenticate_claims(claims, require_mfa=True)
    if not user_id:
        raise HTTPException(status_code=500, detail="Dev user provisioning failed")
    request.session["session_token"] = create_session(user_id)
    write_audit_event(
        action="auth.login", entity_type="user", entity_id=user_id, actor_user_id=user_id,
        request_id=getattr(request.state, "request_id", "dev-login"),
        metadata={"dev_auth": True, "persona": persona.persona},
    )
    return RedirectResponse(persona.landing, status_code=303)
