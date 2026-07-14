from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.workflow_automation import (complete_step, decide_approval, evaluate_sla,
    launch_workflow, list_templates, process_event, request_approval, transition_workflow,
    workflow_detail, workflow_metrics)

router = APIRouter(tags=["workflow-automation"])
templates = Jinja2Templates(directory="app/templates")

class Launch(BaseModel):
    template_code: str; person_id: Optional[int] = None; household_id: Optional[int] = None
    version: Optional[int] = None; priority: str = "normal"; context: dict = {}; idempotency_key: Optional[str] = None
class Transition(BaseModel): reason: Optional[str] = None
class ApprovalRequest(BaseModel): approver_user_id: Optional[int] = None; approver_team_id: Optional[int] = None
class ApprovalDecision(BaseModel): decision: str; notes: Optional[str] = None
class DomainEvent(BaseModel): event_type: str; entity_type: str; entity_id: int; payload: dict = {}; idempotency_key: str

@router.get("/workflows")
def workflow_page(request: Request, principal: Principal = Depends(require_capability("work.read"))):
    return templates.TemplateResponse(request=request, name="workflows/index.html", context={"templates": list_templates(), "metrics": workflow_metrics(), "principal": principal})

@router.get("/workflows/{instance_id}")
def workflow_instance_page(instance_id: int, request: Request, principal: Principal = Depends(require_capability("work.read"))):
    try: data = workflow_detail(instance_id, principal)
    except ValueError as exc: raise HTTPException(404, str(exc))
    except PermissionError as exc: raise HTTPException(403, str(exc))
    return templates.TemplateResponse(request=request, name="workflows/detail.html", context={"data": data, "principal": principal})

@router.get("/api/v1/workflows/templates")
def api_templates(principal: Principal = Depends(require_capability("work.read"))): return {"templates": list_templates()}
@router.post("/api/v1/workflows", status_code=201)
def api_launch(payload: Launch, request: Request, principal: Principal = Depends(require_capability("work.write"))):
    try: return {"id": launch_workflow(**payload.dict(), actor_user_id=principal.user_id, request_id=request.state.request_id)}
    except ValueError as exc: raise HTTPException(400, str(exc))
@router.get("/api/v1/workflows/{instance_id}")
def api_detail(instance_id: int, principal: Principal = Depends(require_capability("work.read"))):
    try: return workflow_detail(instance_id, principal)
    except ValueError as exc: raise HTTPException(404, str(exc))
    except PermissionError as exc: raise HTTPException(403, str(exc))

def _transition(instance_id, action, payload, request, principal):
    try: return {"status": transition_workflow(instance_id, action, actor_user_id=principal.user_id, reason=payload.reason, request_id=request.state.request_id)}
    except ValueError as exc: raise HTTPException(409, str(exc))

@router.post("/api/v1/workflows/{instance_id}/pause")
def pause(instance_id: int, payload: Transition, request: Request, principal: Principal = Depends(require_capability("work.write"))): return _transition(instance_id, "pause", payload, request, principal)
@router.post("/api/v1/workflows/{instance_id}/resume")
def resume(instance_id: int, payload: Transition, request: Request, principal: Principal = Depends(require_capability("work.write"))): return _transition(instance_id, "resume", payload, request, principal)
@router.post("/api/v1/workflows/{instance_id}/cancel")
def cancel(instance_id: int, payload: Transition, request: Request, principal: Principal = Depends(require_capability("work.write"))): return _transition(instance_id, "cancel", payload, request, principal)
@router.post("/api/v1/workflows/{instance_id}/complete")
def complete(instance_id: int, payload: Transition, request: Request, principal: Principal = Depends(require_capability("work.write"))): return _transition(instance_id, "complete", payload, request, principal)
@router.post("/api/v1/workflows/{instance_id}/reopen")
def reopen(instance_id: int, payload: Transition, request: Request, principal: Principal = Depends(require_capability("work.write"))): return _transition(instance_id, "reopen", payload, request, principal)
@router.post("/api/v1/workflows/steps/{step_id}/complete", status_code=204)
def api_complete_step(step_id: int, request: Request, principal: Principal = Depends(require_capability("work.write"))):
    try: complete_step(step_id, actor_user_id=principal.user_id, request_id=request.state.request_id)
    except ValueError as exc: raise HTTPException(409, str(exc))
@router.post("/api/v1/workflows/steps/{step_id}/approvals", status_code=201)
def api_request_approval(step_id: int, payload: ApprovalRequest, principal: Principal = Depends(require_capability("work.write"))):
    try: return {"id": request_approval(step_id, requested_by_user_id=principal.user_id, **payload.dict())}
    except ValueError as exc: raise HTTPException(400, str(exc))
@router.post("/api/v1/workflows/approvals/{approval_id}/decision")
def api_decide(approval_id: int, payload: ApprovalDecision, principal: Principal = Depends(require_capability("work.approve"))):
    try: decide_approval(approval_id, approver_user_id=principal.user_id, **payload.dict()); return {"status": payload.decision}
    except ValueError as exc: raise HTTPException(409, str(exc))
@router.post("/api/v1/workflows/events")
def api_event(payload: DomainEvent, principal: Principal = Depends(require_capability("work.write"))): return {"workflow_ids": process_event(**payload.dict(), actor_user_id=principal.user_id)}
@router.post("/api/v1/workflows/automation/sla")
def api_sla(principal: Principal = Depends(require_capability("work.write"))): return {"escalation_ids": evaluate_sla()}
@router.get("/api/v1/workflows/metrics")
def api_workflow_metrics(principal: Principal = Depends(require_capability("capacity.read"))): return workflow_metrics()
