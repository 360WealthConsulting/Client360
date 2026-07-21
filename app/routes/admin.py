from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select

from app.db import audit_events, engine
from app.security.audit import write_audit_event
from app.security.dependencies import require_capability
from app.security.models import Principal
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
    except ValueError as exc: raise HTTPException(400, str(exc))
    if not changed: raise HTTPException(404, "User not found")
    audit(request, principal, "identity.status_changed", "user", user_id, {"status": payload.status}); return {"status": payload.status}

@router.post("/user-roles")
def create_user_role(payload: RoleAssignment, request: Request, principal: Principal = Depends(require_capability("role.manage"))):
    try: item_id = assign_role(payload.user_id, payload.role_id, actor_capabilities=principal.capabilities)
    except PermissionError as exc:
        write_audit_event(action="authorization.role_assign_denied", entity_type="user", entity_id=payload.user_id, actor_user_id=principal.user_id, outcome="denied", request_id=request.state.request_id, metadata={"role_id": payload.role_id, "detail": str(exc)}); raise HTTPException(403, str(exc))
    except ValueError as exc: raise HTTPException(404, str(exc))
    audit(request, principal, "authorization.role_assigned", "user", payload.user_id, {"role_id": payload.role_id}); return {"id": item_id}

@router.put("/roles/{role_id}/capabilities")
def update_role(role_id: int, payload: RoleComposition, request: Request, principal: Principal = Depends(require_capability("role.manage"))):
    try: compose_role(role_id, payload.capability_ids, actor_capabilities=principal.capabilities)
    except PermissionError as exc:
        write_audit_event(action="authorization.role_compose_denied", entity_type="role", entity_id=role_id, actor_user_id=principal.user_id, outcome="denied", request_id=request.state.request_id, metadata={"capability_ids": payload.capability_ids, "detail": str(exc)}); raise HTTPException(403, str(exc))
    except ValueError as exc: raise HTTPException(404, str(exc))
    audit(request, principal, "authorization.role_composed", "role", role_id, {"capability_ids": payload.capability_ids}); return {"role_id": role_id}

@router.post("/team-memberships")
def create_membership(payload: TeamMembership, request: Request, principal: Principal = Depends(require_capability("team.manage"))):
    item_id = add_team_membership(payload.user_id, payload.team_id, payload.membership_role); audit(request, principal, "team.membership_added", "team", payload.team_id, {"user_id": payload.user_id}); return {"id": item_id}

@router.post("/assignments")
def create_assignment(payload: RecordAssignment, request: Request, principal: Principal = Depends(require_capability("assignment.manage"))):
    try: item_id = assign_record(**payload.dict())
    except ValueError as exc: raise HTTPException(400, str(exc))
    audit(request, principal, "assignment.created", payload.entity_type, payload.entity_id, {"user_id": payload.user_id, "assignment_type": payload.assignment_type}); return {"id": item_id}

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
