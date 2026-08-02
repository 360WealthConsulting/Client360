from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select

from app.db import audit_events, engine
from app.security.audit import write_audit_event
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import employee_admin as ea
from app.services.compliance.rule_catalog import RuleCatalog
from app.services.identity import (
    add_team_membership,
    assign_record,
    assign_role,
    compose_role,
    invite_user,
    list_identity_data,
    set_user_status,
)
from app.templating import render_error

router = APIRouter(prefix="/admin", tags=["administration"])
templates = Jinja2Templates(directory="app/templates")

class UserInvite(BaseModel): email: str; display_name: str; auth_subject: str | None = None
class StatusChange(BaseModel): status: str
class RoleAssignment(BaseModel): user_id: int; role_id: int
class RoleComposition(BaseModel): capability_ids: list[int]
class TeamMembership(BaseModel): user_id: int; team_id: int; membership_role: str = "member"
class RecordAssignment(BaseModel): user_id: int; entity_type: str; entity_id: int; assignment_type: str; team_id: int | None = None

def audit(request, principal, action, entity_type, entity_id=None, metadata=None):
    write_audit_event(action=action, entity_type=entity_type, entity_id=entity_id, actor_user_id=principal.user_id, request_id=request.state.request_id, ip_address=request.client.host if request.client else None, user_agent=request.headers.get("user-agent"), metadata=metadata)

@router.get("")
def administration(request: Request, principal: Principal = Depends(require_capability("identity.manage"))):
    return templates.TemplateResponse(request=request, name="admin/identity.html", context={"identity": list_identity_data(), "principal": principal})

@router.post("/users")
def create_user(payload: UserInvite, request: Request, principal: Principal = Depends(require_capability("identity.manage"))):
    user_id = invite_user(payload.email, payload.display_name, payload.auth_subject); audit(request, principal, "identity.user_invited", "user", user_id); return {"id": user_id}

@router.patch("/users/{user_id}/status")
def change_status(user_id: int, payload: StatusChange, request: Request, principal: Principal = Depends(require_capability("identity.manage"))):
    try: changed = set_user_status(user_id, payload.status)
    except ValueError as exc: raise HTTPException(400, str(exc)) from exc
    if not changed: raise HTTPException(404, "User not found")
    audit(request, principal, "identity.status_changed", "user", user_id, {"status": payload.status}); return {"status": payload.status}

@router.post("/user-roles")
def create_user_role(payload: RoleAssignment, request: Request, principal: Principal = Depends(require_capability("role.manage"))):
    try: item_id = assign_role(payload.user_id, payload.role_id, actor_capabilities=principal.capabilities)
    except PermissionError as exc:
        write_audit_event(action="authorization.role_assign_denied", entity_type="user", entity_id=payload.user_id, actor_user_id=principal.user_id, outcome="denied", request_id=request.state.request_id, metadata={"role_id": payload.role_id, "detail": str(exc)}); raise HTTPException(403, str(exc)) from exc
    except ValueError as exc: raise HTTPException(404, str(exc)) from exc
    audit(request, principal, "authorization.role_assigned", "user", payload.user_id, {"role_id": payload.role_id}); return {"id": item_id}

@router.put("/roles/{role_id}/capabilities")
def update_role(role_id: int, payload: RoleComposition, request: Request, principal: Principal = Depends(require_capability("role.manage"))):
    try: compose_role(role_id, payload.capability_ids, actor_capabilities=principal.capabilities)
    except PermissionError as exc:
        write_audit_event(action="authorization.role_compose_denied", entity_type="role", entity_id=role_id, actor_user_id=principal.user_id, outcome="denied", request_id=request.state.request_id, metadata={"capability_ids": payload.capability_ids, "detail": str(exc)}); raise HTTPException(403, str(exc)) from exc
    except ValueError as exc: raise HTTPException(404, str(exc)) from exc
    audit(request, principal, "authorization.role_composed", "role", role_id, {"capability_ids": payload.capability_ids}); return {"role_id": role_id}

@router.post("/team-memberships")
def create_membership(payload: TeamMembership, request: Request, principal: Principal = Depends(require_capability("team.manage"))):
    item_id = add_team_membership(payload.user_id, payload.team_id, payload.membership_role); audit(request, principal, "team.membership_added", "team", payload.team_id, {"user_id": payload.user_id}); return {"id": item_id}

@router.post("/assignments")
def create_assignment(payload: RecordAssignment, request: Request, principal: Principal = Depends(require_capability("assignment.manage"))):
    try: item_id = assign_record(**payload.dict())
    except ValueError as exc: raise HTTPException(400, str(exc)) from exc
    audit(request, principal, "assignment.created", payload.entity_type, payload.entity_id, {"user_id": payload.user_id, "assignment_type": payload.assignment_type}); return {"id": item_id}

# --- Employee & Access Management UI (administrator-only) ---------------------
# All routes below fall under the middleware `^/admin` rule (identity.manage); role-changing routes
# additionally require role.manage at the handler. Every mutation is audited; the final active
# administrator can never be removed or deactivated. No route writes the DB outside the identity
# service layer, and no capability is created from the UI.

def _back(url: str, ok: str | None = None, err: str | None = None):
    from urllib.parse import quote
    sep = "&" if "?" in url else "?"
    if ok:
        return RedirectResponse(f"{url}{sep}ok={quote(ok)}", status_code=303)
    if err:
        return RedirectResponse(f"{url}{sep}err={quote(err)}", status_code=303)
    return RedirectResponse(url, status_code=303)


@router.get("/employees")
def employees_list(request: Request, principal: Principal = Depends(require_capability("identity.manage"))):
    return templates.TemplateResponse(request=request, name="admin/employees.html", context={
        "principal": principal, "employees": ea.roster(),
        "ok": request.query_params.get("ok"), "err": request.query_params.get("err")})


@router.get("/employees/{user_id}")
def employee_detail(user_id: int, request: Request,
                    principal: Principal = Depends(require_capability("identity.manage"))):
    detail = ea.employee_detail(user_id)
    if detail is None:
        return render_error(request, 404, detail="Employee not found.")
    return templates.TemplateResponse(request=request, name="admin/employee_detail.html", context={
        "principal": principal, "d": detail, "is_last_admin": ea.is_last_active_administrator(user_id),
        "ok": request.query_params.get("ok"), "err": request.query_params.get("err")})


@router.post("/employees/invite")
def employee_invite(request: Request, email: str = Form(...), display_name: str = Form(...),
                    role_id: int | None = Form(None),
                    principal: Principal = Depends(require_capability("identity.manage"))):
    user_id = invite_user(email, display_name)
    audit(request, principal, "identity.user_invited", "user", user_id, {"email": email})
    if role_id:
        try:
            assign_role(user_id, role_id, actor_capabilities=principal.capabilities)
            audit(request, principal, "authorization.role_assigned", "user", user_id, {"role_id": role_id})
        except (PermissionError, ValueError) as exc:
            return _back(f"/admin/employees/{user_id}", err=f"Invited, but role not assigned: {exc}")
    return _back(f"/admin/employees/{user_id}", ok="Employee invited.")


@router.post("/employees/{user_id}/status")
def employee_status(user_id: int, request: Request, status: str = Form(...),
                    principal: Principal = Depends(require_capability("identity.manage"))):
    if status == "disabled" and ea.is_last_active_administrator(user_id):
        return _back(f"/admin/employees/{user_id}",
                     err="Cannot deactivate the final active administrator.")
    try:
        changed = set_user_status(user_id, status)
    except ValueError as exc:
        return _back(f"/admin/employees/{user_id}", err=str(exc))
    if not changed:
        return render_error(request, 404, detail="Employee not found.")
    audit(request, principal, "identity.status_changed", "user", user_id, {"status": status})
    return _back(f"/admin/employees/{user_id}", ok=f"Account {status}.")


@router.post("/employees/{user_id}/identity")
def employee_identity(user_id: int, request: Request, email: str | None = Form(None),
                      auth_subject: str | None = Form(None),
                      principal: Principal = Depends(require_capability("identity.manage"))):
    try:
        if email:
            if ea.update_email(user_id, email):
                audit(request, principal, "identity.email_changed", "user", user_id, {"email": email})
        if auth_subject is not None:
            if ea.set_auth_subject(user_id, auth_subject):
                audit(request, principal, "identity.subject_mapped", "user", user_id,
                      {"entra_subject_set": bool(auth_subject.strip())})
    except ValueError as exc:
        return _back(f"/admin/employees/{user_id}", err=str(exc))
    return _back(f"/admin/employees/{user_id}", ok="Identity updated.")


@router.post("/employees/{user_id}/roles")
def employee_assign_role(user_id: int, request: Request, role_id: int = Form(...),
                         principal: Principal = Depends(require_capability("role.manage"))):
    try:
        assign_role(user_id, role_id, actor_capabilities=principal.capabilities)
    except PermissionError as exc:
        write_audit_event(action="authorization.role_assign_denied", entity_type="user",
                          entity_id=user_id, actor_user_id=principal.user_id, outcome="denied",
                          request_id=request.state.request_id, metadata={"role_id": role_id, "detail": str(exc)})
        return _back(f"/admin/employees/{user_id}", err=str(exc))
    except ValueError as exc:
        return _back(f"/admin/employees/{user_id}", err=str(exc))
    audit(request, principal, "authorization.role_assigned", "user", user_id, {"role_id": role_id})
    return _back(f"/admin/employees/{user_id}", ok="Role assigned.")


@router.post("/employees/{user_id}/roles/remove")
def employee_remove_role(user_id: int, request: Request, role_id: int = Form(...),
                         principal: Principal = Depends(require_capability("role.manage"))):
    # Never strip administrator from the last active admin.
    if ea.role_code(role_id) == ea.ADMIN_ROLE_CODE and ea.is_last_active_administrator(user_id):
        return _back(f"/admin/employees/{user_id}",
                     err="Cannot remove administrator from the final active administrator.")
    changed = ea.end_role(user_id, role_id)
    if changed:
        audit(request, principal, "authorization.role_removed", "user", user_id, {"role_id": role_id})
    return _back(f"/admin/employees/{user_id}", ok="Role removed." if changed else "No active role to remove.")


@router.get("/access-profiles")
def access_profiles(request: Request, principal: Principal = Depends(require_capability("identity.manage"))):
    return templates.TemplateResponse(request=request, name="admin/access_profiles.html", context={
        "principal": principal, "profiles": ea.access_profiles()})


@router.get("/invitations")
def invitations(request: Request, principal: Principal = Depends(require_capability("identity.manage"))):
    roster = ea.roster()
    pending = [e for e in roster if e["access_status"] != "active"]
    return templates.TemplateResponse(request=request, name="admin/invitations.html", context={
        "principal": principal, "pending": pending, "profiles": ea.access_profiles(),
        "ok": request.query_params.get("ok"), "err": request.query_params.get("err")})


@router.get("/audit")
def audit_log(request: Request, limit: int = 100, principal: Principal = Depends(require_capability("audit.read"))):
    with engine.connect() as connection: rows = connection.execute(select(audit_events).order_by(audit_events.c.occurred_at.desc()).limit(min(max(limit, 1), 500))).mappings().all()
    audit(request, principal, "audit.viewed", "audit_event", metadata={"limit": limit}); return templates.TemplateResponse(request=request, name="admin/audit.html", context={"events": rows})


@router.get("/rule-catalog")
def rule_catalog(request: Request, q: str | None = None, category: str | None = None,
                 gate: str | None = None, status: str | None = None, sort: str = "rule_id",
                 desc: bool = False,
                 principal: Principal = Depends(require_capability("audit.read"))):
    """Read-only Rule Catalog — the Phase D.6 governance view over the Advisor
    Intelligence registry. It only reads registry metadata (never executes rules,
    never modifies Advisor Intelligence). No editing/approval/workflow controls."""
    catalog = RuleCatalog.from_registry()
    rules = catalog.query(search=q, category=category, policy_gate=gate,
                          approval_status=status, sort=sort, descending=desc)
    return templates.TemplateResponse(request=request, name="admin/rule_catalog.html", context={
        "principal": principal,
        "rules": rules,
        "categories": catalog.categories(),
        "gates": catalog.policy_gates(),
        "statuses": catalog.approval_statuses(),
        "filters": {"q": q or "", "category": category or "", "gate": gate or "",
                    "status": status or "", "sort": sort, "desc": desc},
        "total": len(catalog.list_rules()),
    })
