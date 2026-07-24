"""Internal Client Portal administration (Phase D.43).

STAFF-facing surface under ``/admin/client-portal/*`` — deliberately NOT under ``/portal`` so it stays on
the internal staff principal + capability RBAC (never the external portal fork). Lets accountable staff
invite portal accounts, revoke access, preview exactly what an account can see (a permissions report built
from the grant scope + visibility registry), and read internal-only diagnostics. There is NO unrestricted
impersonation: staff can preview an account's entitlements but cannot assume its session.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select

from app.db import engine, portal_access_grants, portal_accounts
from app.portal import diagnostics as portal_diagnostics
from app.portal import visibility
from app.portal.service import invite_portal_account, portal_scope
from app.security.audit import write_audit_event
from app.security.authorization import record_in_scope
from app.security.dependencies import require_capability
from app.security.models import Principal

router = APIRouter(prefix="/admin/client-portal", tags=["client-portal-admin"])
templates = Jinja2Templates(directory="app/templates")


class PortalInvite(BaseModel):
    person_id: int
    household_id: int
    email: str
    display_name: str
    access_type: str = "self"
    organization_id: int | None = None


def _audit(request, principal, action, entity_id=None, metadata=None):
    write_audit_event(action=action, entity_type="portal_account", entity_id=entity_id,
                      actor_user_id=principal.user_id, request_id=request.state.request_id,
                      ip_address=request.client.host if request.client else None,
                      user_agent=request.headers.get("user-agent"), metadata=metadata)


def _accounts():
    with engine.connect() as connection:
        return [dict(r) for r in connection.execute(select(
            portal_accounts.c.id, portal_accounts.c.display_name, portal_accounts.c.email,
            portal_accounts.c.status, portal_accounts.c.mfa_enabled, portal_accounts.c.last_login_at,
            portal_accounts.c.person_id).order_by(portal_accounts.c.created_at.desc())).mappings().all()]


@router.get("", response_class=HTMLResponse)
def portal_admin_home(request: Request, principal: Principal = Depends(require_capability("client.read"))):
    return templates.TemplateResponse(request=request, name="admin/client_portal.html",
                                      context={"accounts": _accounts(), "principal": principal})


@router.get("/accounts")
def portal_admin_accounts(principal: Principal = Depends(require_capability("client.read"))):
    return {"accounts": _accounts()}


@router.post("/invite", status_code=201)
def portal_admin_invite(payload: PortalInvite, request: Request,
                        principal: Principal = Depends(require_capability("client.write"))):
    # Record-level scope: staff may only invite for a person they can service.
    if not record_in_scope(principal, "person", payload.person_id, write=True):
        raise HTTPException(403, "Person is outside your record scope")
    account_id, _token = invite_portal_account(
        person_id=payload.person_id, household_id=payload.household_id, email=payload.email,
        display_name=payload.display_name, access_type=payload.access_type,
        invited_by_user_id=principal.user_id, organization_id=payload.organization_id)
    _audit(request, principal, "portal.admin.invited", account_id,
           {"person_id": payload.person_id, "access_type": payload.access_type})
    # The activation token is NEVER returned in the response or logged — delivery is out-of-band.
    return {"account_id": account_id, "status": "invited"}


@router.post("/accounts/{account_id}/revoke", status_code=200)
def portal_admin_revoke(account_id: int, request: Request,
                        principal: Principal = Depends(require_capability("client.write"))):
    from datetime import date
    with engine.begin() as connection:
        acct = connection.execute(select(portal_accounts.c.person_id).where(
            portal_accounts.c.id == account_id)).mappings().one_or_none()
        if not acct:
            raise HTTPException(404, "Portal account not found")
        if not record_in_scope(principal, "person", acct["person_id"], write=True):
            raise HTTPException(403, "Person is outside your record scope")
        connection.execute(portal_accounts.update().where(portal_accounts.c.id == account_id).values(
            status="revoked"))
        connection.execute(portal_access_grants.update().where(
            portal_access_grants.c.portal_account_id == account_id,
            portal_access_grants.c.inactive_date.is_(None)).values(inactive_date=date.today()))
    _audit(request, principal, "portal.admin.revoked", account_id)
    return {"account_id": account_id, "status": "revoked"}


@router.get("/accounts/{account_id}/preview")
def portal_admin_preview(account_id: int, principal: Principal = Depends(require_capability("client.read"))):
    """A permissions report: exactly what this account can see, derived from its grant scope + the
    visibility registry. This is NOT impersonation — no session is created, only entitlements shown."""
    with engine.connect() as connection:
        acct = connection.execute(select(portal_accounts.c.person_id).where(
            portal_accounts.c.id == account_id)).mappings().one_or_none()
    if not acct:
        raise HTTPException(404, "Portal account not found")
    if not record_in_scope(principal, "person", acct["person_id"]):
        raise HTTPException(403, "Person is outside your record scope")
    scope = portal_scope(account_id)
    granted = set()
    for g in scope["grants"]:
        for perm, on in (g["permissions"] or {}).items():
            if on:
                granted.add(perm)
    fields = []
    for f in visibility.external_fields():
        entitled = f.required_permission is None or f.required_permission in granted
        fields.append({"key": f.key, "source": f.source_service, "requires": f.required_permission,
                       "scope": f.required_scope, "masking": f.masking_rule, "entitled": entitled})
    return {"account_id": account_id, "granted_permissions": sorted(granted),
            "household_ids": sorted(scope["household_ids"]), "person_count": len(scope["person_ids"]),
            "visible_fields": fields}


@router.get("/diagnostics")
def portal_admin_diagnostics(principal: Principal = Depends(require_capability("observability.audit"))):
    return portal_diagnostics.portal_diagnostics()
