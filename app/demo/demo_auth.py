"""Demo login router (DEMO-ONLY).

Provides a `/demo/login` page that lets a developer sign in as any seeded demo
persona. It is NOT an auth bypass: a password is required, and on success it
issues a session through the SAME `authenticate_claims` + `create_session`
(staff) or `create_portal_session` (portal) code paths the real app uses, with
real capabilities and audit. This router is only mounted by the demo entrypoint
(`app.demo.demo_app`), which never loads in production.
"""
import secrets
from html import escape

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.db import engine, portal_accounts
from app.integrations.identity.base import IdentityClaims
from app.security.audit import write_audit_event
from app.security.service import authenticate_claims, create_session
from app.portal.service import create_portal_session
from app.demo.credentials import DEMO_PORTAL, DEMO_STAFF, staff_by_username

router = APIRouter(prefix="/demo", tags=["demo"])


def _page(body: str, status_code: int = 200) -> HTMLResponse:
    rows = "".join(
        f"<tr><td>{escape(u.persona)}</td><td><code>{escape(u.username)}</code></td>"
        f"<td><code>{escape(u.password)}</code></td><td>{escape(u.display_name)}</td></tr>"
        for u in DEMO_STAFF
    )
    rows += (
        f"<tr><td>{escape(DEMO_PORTAL.persona)}</td><td><code>{escape(DEMO_PORTAL.username)}</code></td>"
        f"<td><code>{escape(DEMO_PORTAL.password)}</code></td><td>{escape(DEMO_PORTAL.display_name)}</td></tr>"
    )
    return HTMLResponse(
        f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
        <title>Client360 — Developer Demo Login</title>
        <style>
          body {{ font-family: Arial, sans-serif; max-width: 760px; margin: 40px auto; color:#1f2937; }}
          h1 {{ color:#111827; }} .demo-banner {{ background:#fef3c7; border-left:4px solid #d97706; padding:10px 14px; }}
          table {{ border-collapse: collapse; width:100%; margin-top:16px; }} td,th {{ border:1px solid #e5e7eb; padding:8px; text-align:left; }}
          form {{ margin-top:20px; padding:16px; background:#f9fafb; border-radius:8px; }}
          input,button {{ padding:8px; font-size:14px; }} button {{ background:#2563eb; color:#fff; border:0; border-radius:6px; cursor:pointer; }}
          .err {{ color:#b91c1c; }} code {{ background:#f3f4f6; padding:1px 4px; }}
        </style></head><body>
        <div class="demo-banner"><strong>Developer Demo Mode</strong> — fictional data only, demo database. Not for production.</div>
        <h1>Client360 Demo Login</h1>
        {body}
        <form method="post" action="/demo/login">
          <label>Username <input name="username" autocomplete="off" required></label>
          <label>Password <input name="password" type="password" autocomplete="off" required></label>
          <button type="submit">Sign in</button>
        </form>
        <h2>Demo credentials</h2>
        <table><tr><th>Persona</th><th>Username</th><th>Password</th><th>Name</th></tr>{rows}</table>
        </body></html>""",
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
def demo_home():
    return RedirectResponse("/demo/login", 303)


@router.get("/login", response_class=HTMLResponse)
def demo_login_page():
    return _page("<p>Sign in as any seeded demo persona.</p>")


@router.post("/login")
def demo_login(request: Request, username: str = Form(...), password: str = Form(...)):
    # Portal persona → real portal session.
    if username == DEMO_PORTAL.username:
        if not secrets.compare_digest(password, DEMO_PORTAL.password):
            return _page('<p class="err">Invalid demo credentials.</p>', 401)
        with engine.connect() as connection:
            account_id = connection.scalar(
                select(portal_accounts.c.id).where(
                    portal_accounts.c.email == DEMO_PORTAL.email,
                    portal_accounts.c.status == "active",
                )
            )
        if not account_id:
            return _page('<p class="err">Portal demo account is not seeded. Run the demo seed first.</p>', 409)
        token = create_portal_session(
            account_id, device_fingerprint="demo-portal-device", device_name="Demo Browser",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        request.session["portal_session_token"] = token
        return RedirectResponse("/portal/", 303)

    # Staff persona → real staff session (authenticate_claims + create_session).
    user = staff_by_username(username)
    if not user or not secrets.compare_digest(password, user.password):
        return _page('<p class="err">Invalid demo credentials.</p>', 401)
    claims = IdentityClaims(
        subject=user.auth_subject, email=user.email,
        display_name=user.display_name, mfa_authenticated=True,
    )
    user_id = authenticate_claims(claims, require_mfa=True)
    if not user_id:
        return _page('<p class="err">Demo staff user is not seeded. Run the demo seed first.</p>', 409)
    request.session["session_token"] = create_session(user_id)
    write_audit_event(
        action="auth.login", entity_type="user", entity_id=user_id, actor_user_id=user_id,
        request_id=getattr(request.state, "request_id", "demo-login"),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"), metadata={"demo": True},
    )
    return RedirectResponse("/", 303)
