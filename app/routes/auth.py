import os
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.integrations.identity import OidcIdentityProvider
from app.security.audit import write_audit_event
from app.security.service import authenticate_claims, create_session, revoke_session

router = APIRouter(prefix="/auth", tags=["authentication"])

def _safe_next(value: str | None) -> str | None:
    """Accept only a local path (``/...``) as the post-login return URL — never an absolute/off-site
    URL or a protocol-relative ``//host`` (open-redirect guard)."""
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return None


@router.get("/login", response_class=HTMLResponse)
def login(request: Request, next: str | None = None):
    state = secrets.token_urlsafe(24)
    request.session["oidc_state"] = state
    safe_next = _safe_next(next)
    if safe_next:
        request.session["post_login_redirect"] = safe_next
    redirect_uri = str(request.url_for("auth_callback"))
    try: url = OidcIdentityProvider().authorization_url(state=state, redirect_uri=redirect_uri)
    except RuntimeError: return HTMLResponse("<h1>Client360 sign-in is not configured</h1><p>Configure the OIDC provider before enabling staff access.</p>", 503)
    return RedirectResponse(url, 303)

@router.get("/callback", name="auth_callback")
def auth_callback(request: Request, code: str, state: str):
    if not state or not secrets.compare_digest(state, request.session.pop("oidc_state", "")): raise HTTPException(400, "Invalid authentication state")
    claims = OidcIdentityProvider().exchange_code(code=code, redirect_uri=str(request.url_for("auth_callback")))
    user_id = authenticate_claims(claims, os.getenv("OIDC_REQUIRE_MFA", "true").lower() == "true")
    if not user_id: raise HTTPException(403, "Account is inactive, uninvited, or missing required MFA")
    request.session["session_token"] = create_session(user_id)
    write_audit_event(action="auth.login", entity_type="user", entity_id=user_id, actor_user_id=user_id, request_id=request.state.request_id, ip_address=request.client.host if request.client else None, user_agent=request.headers.get("user-agent"))
    # Return the employee to where they were headed (validated local path), else the Staff Home dashboard.
    destination = _safe_next(request.session.pop("post_login_redirect", None)) or "/home"
    return RedirectResponse(destination, 303)

@router.post("/logout")
def logout(request: Request):
    token = request.session.pop("session_token", None)
    principal = request.state.principal
    if token: revoke_session(token)
    write_audit_event(action="auth.logout", entity_type="user", entity_id=principal.user_id, actor_user_id=principal.user_id, request_id=request.state.request_id)
    return RedirectResponse("/auth/login", 303)
