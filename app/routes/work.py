from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.security.audit import audit_denied
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.work_management import (
    apply_assignment_rules, assign_work, authorize_assignment_target,
    authorize_existing_assignment, dashboard, deactivate_assignment,
    list_assignments, queue_detail, reassign_work,
)

router = APIRouter(tags=["work-management"])
templates = Jinja2Templates(directory="app/templates")


class AssignmentCreate(BaseModel):
    entity_type: str; entity_id: int; assignment_role: str
    user_id: Optional[int] = None; team_id: Optional[int] = None
    reason: Optional[str] = None


class Reassignment(BaseModel):
    user_id: Optional[int] = None; team_id: Optional[int] = None
    reason: Optional[str] = None


class AutomaticAssignment(BaseModel):
    entity_type: str; entity_id: int; attributes: dict


def filters(priority=None, status=None, team_id=None, due_before=None, queue=None,
            assignee=None, workflow=None):
    return {"priority": priority, "status": status, "team_id": team_id,
            "due_before": due_before, "queue": queue, "assignee": assignee,
            "workflow": workflow}


@router.get("/work")
def my_work(request: Request, priority: Optional[str] = None, status: Optional[str] = None,
            team_id: Optional[int] = None, due_before: Optional[date] = None,
            queue: Optional[str] = None, workflow: Optional[str] = None,
            principal: Principal = Depends(require_capability("work.read"))):
    data = dashboard(principal, filters(priority, status, team_id, due_before, queue=queue, workflow=workflow))
    return templates.TemplateResponse(request=request, name="work/my_work.html", context={"work": data, "principal": principal, "filters": request.query_params})


@router.get("/work/team")
def team_work(request: Request, principal: Principal = Depends(require_capability("capacity.read"))):
    return templates.TemplateResponse(request=request, name="work/team_work.html", context={"work": dashboard(principal), "principal": principal})


@router.get("/work/queues/{code}")
def queue_view(code: str, request: Request, principal: Principal = Depends(require_capability("work.read"))):
    try: data = queue_detail(principal, code)
    except ValueError as exc: raise HTTPException(404, str(exc))
    return templates.TemplateResponse(request=request, name="work/queue.html", context={"data": data, "principal": principal})


@router.post("/work/assignments/{entity_type}/{entity_id}")
async def ui_assign(entity_type: str, entity_id: int, request: Request,
                    principal: Principal = Depends(require_capability("work.write"))):
    form = await request.form()
    try:
        authorize_assignment_target(principal, entity_type, entity_id)
        assignment_id = assign_work(
            entity_type=entity_type, entity_id=entity_id,
            assignment_role=str(form.get("assignment_role") or "secondary"),
            user_id=int(form["user_id"]) if form.get("user_id") else None,
            team_id=int(form["team_id"]) if form.get("team_id") else None,
            reason=str(form.get("reason") or "") or None,
            actor_user_id=principal.user_id, request_id=request.state.request_id,
        )
    except PermissionError as exc:
        audit_denied(request, action="assignment.create_denied", entity_type=entity_type, entity_id=entity_id, actor_user_id=principal.user_id, detail=str(exc))
        raise HTTPException(403, str(exc))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc))
    return RedirectResponse(url=f"/work?assigned={assignment_id}", status_code=303)


@router.get("/api/v1/work/my-work")
def api_my_work(priority: Optional[str] = None, status: Optional[str] = None,
                team_id: Optional[int] = None, due_before: Optional[date] = None,
                principal: Principal = Depends(require_capability("work.read"))):
    return dashboard(principal, filters(priority, status, team_id, due_before))


@router.get("/api/v1/work/team-work")
def api_team_work(principal: Principal = Depends(require_capability("capacity.read"))): return dashboard(principal)


@router.get("/api/v1/work/queues")
def api_queues(principal: Principal = Depends(require_capability("work.read"))): return {"queues": dashboard(principal)["queues"]}


@router.get("/api/v1/work/assignments")
def api_assignments(principal: Principal = Depends(require_capability("work.read"))): return {"assignments": list_assignments(principal)}


@router.get("/api/v1/work/queues/{code}")
def api_queue(code: str, principal: Principal = Depends(require_capability("work.read"))):
    try: return queue_detail(principal, code)
    except ValueError as exc: raise HTTPException(404, str(exc))


@router.get("/api/v1/work/capacity")
def api_capacity(principal: Principal = Depends(require_capability("capacity.read"))): return dashboard(principal)["capacity"]


@router.get("/api/v1/work/dashboard-metrics")
def api_metrics(principal: Principal = Depends(require_capability("work.read"))):
    data = dashboard(principal)
    return {"work_items": len(data["items"]), "assigned_clients": len(data["assigned_people"]),
            "assigned_households": len(data["assigned_households"]), "approvals": len(data["approvals"]),
            "capacity": data["capacity"], "bottlenecks": data["bottlenecks"]}


@router.get("/api/v1/work/daily-agenda")
def api_agenda(principal: Principal = Depends(require_capability("work.read"))): return {"items": dashboard(principal)["items"]}


@router.post("/api/v1/work/assignments", status_code=201)
def api_assign(payload: AssignmentCreate, request: Request, principal: Principal = Depends(require_capability("work.write"))):
    try:
        authorize_assignment_target(principal, payload.entity_type, payload.entity_id)
        assignment_id = assign_work(**payload.dict(), actor_user_id=principal.user_id, request_id=request.state.request_id)
    except PermissionError as exc:
        audit_denied(request, action="assignment.create_denied", entity_type=payload.entity_type, entity_id=payload.entity_id, actor_user_id=principal.user_id, detail=str(exc))
        raise HTTPException(403, str(exc))
    except ValueError as exc: raise HTTPException(400, str(exc))
    return {"id": assignment_id}


@router.post("/api/v1/work/assignments/{assignment_id}/reassign", status_code=201)
def api_reassign(assignment_id: int, payload: Reassignment, request: Request,
                 principal: Principal = Depends(require_capability("work.write"))):
    try:
        if authorize_existing_assignment(principal, assignment_id) is None:
            raise HTTPException(404, "Assignment not found")
        new_id = reassign_work(assignment_id, **payload.dict(), actor_user_id=principal.user_id, request_id=request.state.request_id)
    except PermissionError as exc:
        audit_denied(request, action="assignment.reassign_denied", entity_type="assignment", entity_id=assignment_id, actor_user_id=principal.user_id, detail=str(exc))
        raise HTTPException(403, str(exc))
    except ValueError as exc: raise HTTPException(400, str(exc))
    return {"id": new_id, "replaces": assignment_id}


@router.delete("/api/v1/work/assignments/{assignment_id}", status_code=204)
def api_remove(assignment_id: int, request: Request, principal: Principal = Depends(require_capability("work.write"))):
    try:
        if authorize_existing_assignment(principal, assignment_id) is None:
            raise HTTPException(404, "Assignment not found")
        deactivate_assignment(assignment_id, actor_user_id=principal.user_id, request_id=request.state.request_id)
    except PermissionError as exc:
        audit_denied(request, action="assignment.remove_denied", entity_type="assignment", entity_id=assignment_id, actor_user_id=principal.user_id, detail=str(exc))
        raise HTTPException(403, str(exc))
    except ValueError as exc: raise HTTPException(404, str(exc))


@router.post("/api/v1/work/assignments/automatic", status_code=201)
def api_automatic(payload: AutomaticAssignment, request: Request, principal: Principal = Depends(require_capability("work.write"))):
    try:
        authorize_assignment_target(principal, payload.entity_type, payload.entity_id)
        created = apply_assignment_rules(payload.entity_type, payload.entity_id, payload.attributes, principal.user_id, request.state.request_id)
    except PermissionError as exc:
        audit_denied(request, action="assignment.automatic_denied", entity_type=payload.entity_type, entity_id=payload.entity_id, actor_user_id=principal.user_id, detail=str(exc))
        raise HTTPException(403, str(exc))
    except ValueError as exc: raise HTTPException(400, str(exc))
    return {"assignment_ids": created}
