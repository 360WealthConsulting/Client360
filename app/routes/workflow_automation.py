"""Workflow automation routes (Phase D.17). The orchestration surface over the existing engine.

New ``/workflow-automation`` prefix (the legacy ``/workflows`` routes + ``work.*`` are preserved
and untouched). Outside the middleware RULES, so each endpoint enforces its ``workflow.*``
capability in-route; the service enforces record scope. Sensitive execution history is gated by
``workflow.audit``.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.workflow_orchestration import service as svc
from app.services.workflow_orchestration import triggers
from app.templating import install_filters

router = APIRouter(prefix="/workflow-automation", tags=["workflow"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


async def _form(request):
    return parse_qs((await request.body()).decode("utf-8"))


def _one(form, key):
    return (form.get(key, [""])[0]).strip()


def _int(form, key):
    v = _one(form, key)
    return int(v) if v else None


@router.get("", response_class=HTMLResponse)
def overview(request: Request, status: str | None = None, q: str | None = None, page: int = 1,
             principal: Principal = Depends(require_capability("workflow.view"))):
    result = svc.list_instances(principal, status=status, search=q, page=page)
    return templates.TemplateResponse(request=request, name="workflow_automation/overview.html", context={
        "principal": principal, "result": result, "templates": svc.templates(),
        "metrics": svc.metrics(principal), "filters": {"status": status or "", "q": q or ""},
        "can_execute": principal.can("workflow.execute")})


@router.get("/templates")
def list_templates(request: Request, principal: Principal = Depends(require_capability("workflow.view"))):
    return JSONResponse({"templates": [{"code": t["code"], "version": t["version"], "name": t["name"],
                                        "category": t["category"], "status": t["status"]}
                                       for t in svc.templates()]})


@router.get("/triggers")
def list_triggers(request: Request, principal: Principal = Depends(require_capability("workflow.view"))):
    return JSONResponse({"trigger_types": sorted(triggers.TRIGGER_TYPES),
                         "triggers": triggers.list_triggers(principal)})


@router.get("/{instance_id}", response_class=HTMLResponse)
def detail(request: Request, instance_id: int,
           principal: Principal = Depends(require_capability("workflow.view"))):
    inst = svc.get_instance(principal, instance_id)
    if inst is None:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(request=request, name="workflow_automation/detail.html", context={
        "principal": principal, "w": inst,
        "can_execute": principal.can("workflow.execute"),
        "can_cancel": principal.can("workflow.cancel")})


@router.get("/{instance_id}/audit")
def audit(request: Request, instance_id: int,
          principal: Principal = Depends(require_capability("workflow.audit"))):
    try:
        return JSONResponse({"history": [
            {"event_type": e["event_type"], "step_id": e["workflow_step_id"],
             "occurred_at": e["occurred_at"].isoformat()} for e in svc.audit_history(principal, instance_id)]})
    except svc.WorkflowNotFound as exc:
        raise HTTPException(404, "Not found") from exc


@router.post("/launch")
async def launch(request: Request, principal: Principal = Depends(require_capability("workflow.execute"))):
    form = await _form(request)
    try:
        inst = svc.launch(principal, _one(form, "template_code"), actor_user_id=principal.user_id,
                          person_id=_int(form, "person_id"), household_id=_int(form, "household_id"),
                          priority=_one(form, "priority") or "normal")
    except svc.WorkflowError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/workflow-automation/{inst['workflow']['id']}", status_code=303)


def _lifecycle(action, cap):
    async def handler(request: Request, instance_id: int,
                      principal: Principal = Depends(require_capability(cap))):
        form = await _form(request)
        try:
            svc.transition(principal, instance_id, action, actor_user_id=principal.user_id,
                           reason=_one(form, "reason") or None)
        except svc.WorkflowNotFound as exc:
            raise HTTPException(404, "Not found") from exc
        except svc.WorkflowError as exc:
            return RedirectResponse(url=f"/workflow-automation/{instance_id}?error={exc}", status_code=303)
        return RedirectResponse(url=f"/workflow-automation/{instance_id}", status_code=303)
    return handler


router.add_api_route("/{instance_id}/pause", _lifecycle("pause", "workflow.execute"), methods=["POST"])
router.add_api_route("/{instance_id}/resume", _lifecycle("resume", "workflow.execute"), methods=["POST"])
router.add_api_route("/{instance_id}/cancel", _lifecycle("cancel", "workflow.cancel"), methods=["POST"])


@router.post("/steps/{step_id}/complete")
async def complete_step(request: Request, step_id: int,
                        principal: Principal = Depends(require_capability("workflow.execute"))):
    try:
        inst = svc.complete_step(principal, step_id, actor_user_id=principal.user_id)
    except svc.WorkflowNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.WorkflowError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/workflow-automation/{inst['workflow']['id']}", status_code=303)


@router.post("/steps/{step_id}/retry")
async def retry_step(request: Request, step_id: int,
                     principal: Principal = Depends(require_capability("workflow.execute"))):
    try:
        step = svc.retry_step(principal, step_id, actor_user_id=principal.user_id)
    except svc.WorkflowNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.WorkflowError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/workflow-automation/{step['workflow_instance_id']}", status_code=303)


@router.post("/steps/{step_id}/assign")
async def assign_step(request: Request, step_id: int,
                      principal: Principal = Depends(require_capability("workflow.execute"))):
    form = await _form(request)
    try:
        step = svc.assign_step(principal, step_id, _int(form, "user_id"), actor_user_id=principal.user_id)
    except svc.WorkflowNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.WorkflowError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/workflow-automation/{step['workflow_instance_id']}", status_code=303)


@router.post("/triggers")
async def configure_trigger(request: Request,
                            principal: Principal = Depends(require_capability("workflow.template_manage"))):
    form = await _form(request)
    try:
        triggers.configure_trigger(principal, name=_one(form, "name"),
                                   event_type=_one(form, "event_type"),
                                   template_code=_one(form, "template_code"),
                                   actor_user_id=principal.user_id,
                                   active=(_one(form, "active") == "1"))
    except triggers.TriggerError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/workflow-automation", status_code=303)


@router.post("/triggers/{trigger_id}/active")
async def set_trigger_active(request: Request, trigger_id: int,
                             principal: Principal = Depends(require_capability("workflow.template_manage"))):
    form = await _form(request)
    triggers.set_active(principal, trigger_id, _one(form, "active") == "1")
    return RedirectResponse(url="/workflow-automation", status_code=303)
