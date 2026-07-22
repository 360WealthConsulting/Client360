"""Enterprise Operations routes (Phase D.20) — firm projects, operational tasks, capacity.

New ``/operations`` prefix. It matches no middleware RULE, so each endpoint enforces its
``operations.*`` capability in-route; the service enforces record scope on every read and write.
Operational tasks are routed under ``/operations/.../items`` (NOT ``/tasks``) so they never collide
with the client-task ``/tasks`` middleware rule. Advisor Work remains the authoritative client-work
domain — Operations only references it. Sensitive audit history is gated by ``operations.audit``.
"""
from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.operations import capacity as cap
from app.services.operations import common
from app.services.operations import projects as proj
from app.services.operations import tasks as opstasks
from app.services.operations import templates as tmpl
from app.templating import install_filters

router = APIRouter(prefix="/operations", tags=["operations"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


async def _form(request):
    return parse_qs((await request.body()).decode("utf-8"))


def _one(form, key):
    return (form.get(key, [""])[0]).strip()


def _int(form, key):
    v = _one(form, key)
    return int(v) if v else None


def _date(form, key):
    v = _one(form, key)
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError as exc:
        raise HTTPException(400, f"invalid date for {key!r}") from exc


# --- overview + reads --------------------------------------------------------

@router.get("", response_class=HTMLResponse)
def overview(request: Request, status: str | None = None, category: str | None = None,
             q: str | None = None, page: int = 1,
             principal: Principal = Depends(require_capability("operations.view"))):
    result = proj.list_projects(principal, status=status, category=category, search=q, page=page)
    return templates.TemplateResponse(request=request, name="operations/overview.html", context={
        "principal": principal, "result": result, "metrics": proj.project_metrics(principal),
        "task_metrics": opstasks.task_metrics(principal),
        "capacity": cap.capacity_overview(principal),
        "templates": tmpl.list_templates(active_only=True),
        "resources": tmpl.list_resources(active_only=True),
        "filters": {"status": status or "", "category": category or "", "q": q or ""},
        "can_manage": principal.can("operations.manage"),
        "can_templates": principal.can("operations.templates")})


@router.get("/templates")
def list_templates(request: Request,
                   principal: Principal = Depends(require_capability("operations.view"))):
    return JSONResponse({"templates": [
        {"id": t["id"], "code": t["code"], "name": t["name"], "category": t["category"],
         "active": t["active"]} for t in tmpl.list_templates()]})


@router.get("/resources")
def list_resources(request: Request,
                   principal: Principal = Depends(require_capability("operations.view"))):
    return JSONResponse({"resources": [
        {"id": r["id"], "code": r["code"], "name": r["name"], "resource_type": r["resource_type"],
         "department": r["department"], "capacity_minutes_per_day": r["capacity_minutes_per_day"]}
        for r in tmpl.list_resources()]})


@router.get("/capacity")
def capacity_view(request: Request, department: str | None = None,
                  principal: Principal = Depends(require_capability("operations.view"))):
    overview = cap.capacity_overview(principal, department=department)
    return JSONResponse({"resource_count": overview["resource_count"],
                         "over_capacity_count": overview["over_capacity_count"],
                         "resources": overview["resources"]})


@router.get("/items")
def list_items(request: Request, status: str | None = None, project_id: int | None = None,
               open_only: int = 0, page: int = 1,
               principal: Principal = Depends(require_capability("operations.view"))):
    result = opstasks.list_tasks(principal, status=status, project_id=project_id,
                                 open_only=bool(open_only), page=page)
    return JSONResponse({"total": result["total"], "page": result["page"], "tasks": [
        {"id": t["id"], "title": t["title"], "status": t["status"], "priority": t["priority"],
         "project_id": t["project_id"], "assigned_user_id": t["assigned_user_id"]}
        for t in result["rows"]]})


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(request: Request, project_id: int,
                   principal: Principal = Depends(require_capability("operations.view"))):
    p = proj.get_project(principal, project_id)
    if p is None:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(request=request, name="operations/project.html", context={
        "principal": principal, "p": p, "can_manage": principal.can("operations.manage"),
        "can_audit": principal.can("operations.audit")})


@router.get("/projects/{project_id}/audit")
def project_audit(request: Request, project_id: int,
                  principal: Principal = Depends(require_capability("operations.audit"))):
    if proj.get_project(principal, project_id) is None:
        raise HTTPException(404, "Not found")
    return JSONResponse({"history": [
        {"event_type": e["event_type"], "entity_type": e["entity_type"], "entity_id": e["entity_id"],
         "to_status": e["to_status"], "occurred_at": e["occurred_at"].isoformat()}
        for e in common.audit_history(principal, entity_type="project", entity_id=project_id)]})


@router.get("/items/{task_id}", response_class=HTMLResponse)
def item_detail(request: Request, task_id: int,
                principal: Principal = Depends(require_capability("operations.view"))):
    t = opstasks.get_task(principal, task_id)
    if t is None:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(request=request, name="operations/task.html", context={
        "principal": principal, "t": t, "can_manage": principal.can("operations.manage")})


@router.get("/items/{task_id}/audit")
def item_audit(request: Request, task_id: int,
               principal: Principal = Depends(require_capability("operations.audit"))):
    if opstasks.get_task(principal, task_id) is None:
        raise HTTPException(404, "Not found")
    return JSONResponse({"history": [
        {"event_type": e["event_type"], "to_status": e["to_status"],
         "occurred_at": e["occurred_at"].isoformat()}
        for e in common.audit_history(principal, entity_type="task", entity_id=task_id)]})


# --- projects ----------------------------------------------------------------

@router.post("/projects")
async def create_project(request: Request,
                         principal: Principal = Depends(require_capability("operations.manage"))):
    form = await _form(request)
    try:
        p = proj.create_project(
            principal, name=_one(form, "name"), category=_one(form, "category") or "general",
            priority=_one(form, "priority") or "normal", status=_one(form, "status") or "planned",
            template_code=_one(form, "template_code") or None, department=_one(form, "department") or None,
            description=_one(form, "description") or None, start_date=_date(form, "start_date"),
            target_end_date=_date(form, "target_end_date"), person_id=_int(form, "person_id"),
            household_id=_int(form, "household_id"), organization_id=_int(form, "organization_id"),
            actor_user_id=principal.user_id)
    except common.OperationsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/operations/projects/{p['id']}", status_code=303)


@router.post("/projects/{project_id}/edit")
async def edit_project(request: Request, project_id: int,
                       principal: Principal = Depends(require_capability("operations.manage"))):
    form = await _form(request)
    fields = {k: _one(form, k) for k in ("name", "priority", "category", "health", "department",
                                         "description") if _one(form, k)}
    try:
        proj.update_project(principal, project_id, actor_user_id=principal.user_id, **fields)
    except common.OperationsNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.OperationsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/operations/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/status")
async def project_status(request: Request, project_id: int,
                         principal: Principal = Depends(require_capability("operations.manage"))):
    form = await _form(request)
    try:
        proj.transition_project(principal, project_id, _one(form, "status"),
                                actor_user_id=principal.user_id, reason=_one(form, "reason") or None)
    except common.OperationsNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.OperationsError as exc:
        return RedirectResponse(url=f"/operations/projects/{project_id}?error={exc}", status_code=303)
    return RedirectResponse(url=f"/operations/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/phases")
async def add_phase(request: Request, project_id: int,
                    principal: Principal = Depends(require_capability("operations.manage"))):
    form = await _form(request)
    try:
        proj.add_phase(principal, project_id, name=_one(form, "name"),
                       sequence=_int(form, "sequence") or 0, actor_user_id=principal.user_id)
    except common.OperationsNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.OperationsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/operations/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/milestones")
async def add_milestone(request: Request, project_id: int,
                        principal: Principal = Depends(require_capability("operations.manage"))):
    form = await _form(request)
    try:
        proj.add_milestone(principal, project_id, name=_one(form, "name"),
                           due_date=_date(form, "due_date"), phase_id=_int(form, "phase_id"),
                           actor_user_id=principal.user_id)
    except common.OperationsNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.OperationsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/operations/projects/{project_id}", status_code=303)


@router.post("/milestones/{milestone_id}/reach")
async def reach_milestone(request: Request, milestone_id: int,
                          principal: Principal = Depends(require_capability("operations.manage"))):
    try:
        ms = proj.reach_milestone(principal, milestone_id, actor_user_id=principal.user_id)
    except common.OperationsNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.OperationsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/operations/projects/{ms['project_id']}", status_code=303)


# --- operational tasks (routed as /items) ------------------------------------

def _create_task_from_form(principal, form, *, project_id=None):
    return opstasks.create_task(
        principal, title=_one(form, "title"), project_id=project_id or _int(form, "project_id"),
        phase_id=_int(form, "phase_id"), description=_one(form, "description") or None,
        priority=_one(form, "priority") or "normal", department=_one(form, "department") or None,
        estimated_minutes=_int(form, "estimated_minutes"), due_date=_date(form, "due_date"),
        assigned_user_id=_int(form, "assigned_user_id"),
        assigned_resource_id=_int(form, "assigned_resource_id"), person_id=_int(form, "person_id"),
        household_id=_int(form, "household_id"), actor_user_id=principal.user_id)


@router.post("/projects/{project_id}/items")
async def create_project_task(request: Request, project_id: int,
                              principal: Principal = Depends(require_capability("operations.manage"))):
    form = await _form(request)
    try:
        _create_task_from_form(principal, form, project_id=project_id)
    except common.OperationsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/operations/projects/{project_id}", status_code=303)


@router.post("/items")
async def create_task(request: Request,
                      principal: Principal = Depends(require_capability("operations.manage"))):
    form = await _form(request)
    try:
        t = _create_task_from_form(principal, form)
    except common.OperationsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/operations/items/{t['id']}", status_code=303)


@router.post("/items/{task_id}/status")
async def item_status(request: Request, task_id: int,
                      principal: Principal = Depends(require_capability("operations.manage"))):
    form = await _form(request)
    try:
        opstasks.transition_task(principal, task_id, _one(form, "status"),
                                 actor_user_id=principal.user_id, reason=_one(form, "reason") or None)
    except common.OperationsNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.OperationsError as exc:
        return RedirectResponse(url=f"/operations/items/{task_id}?error={exc}", status_code=303)
    return RedirectResponse(url=f"/operations/items/{task_id}", status_code=303)


@router.post("/items/{task_id}/assign")
async def item_assign(request: Request, task_id: int,
                      principal: Principal = Depends(require_capability("operations.manage"))):
    form = await _form(request)
    try:
        opstasks.assign_task(principal, task_id, assigned_user_id=_int(form, "assigned_user_id"),
                             assigned_resource_id=_int(form, "assigned_resource_id"),
                             actor_user_id=principal.user_id)
    except common.OperationsNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.OperationsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/operations/items/{task_id}", status_code=303)


@router.post("/items/{task_id}/dependencies")
async def item_dependency(request: Request, task_id: int,
                          principal: Principal = Depends(require_capability("operations.manage"))):
    form = await _form(request)
    try:
        opstasks.add_dependency(principal, task_id, _int(form, "depends_on_task_id"),
                                dependency_type=_one(form, "dependency_type") or "finish_to_start",
                                actor_user_id=principal.user_id)
    except common.OperationsNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.OperationsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/operations/items/{task_id}", status_code=303)


@router.post("/items/{task_id}/checklist")
async def item_checklist(request: Request, task_id: int,
                         principal: Principal = Depends(require_capability("operations.manage"))):
    form = await _form(request)
    try:
        opstasks.add_checklist_item(principal, task_id, description=_one(form, "description"),
                                    position=_int(form, "position") or 0,
                                    actor_user_id=principal.user_id)
    except common.OperationsNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.OperationsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/operations/items/{task_id}", status_code=303)


@router.post("/checklist/{item_id}/toggle")
async def toggle_checklist(request: Request, item_id: int,
                           principal: Principal = Depends(require_capability("operations.manage"))):
    form = await _form(request)
    try:
        row = opstasks.toggle_checklist_item(principal, item_id, done=(_one(form, "done") != "0"),
                                             actor_user_id=principal.user_id)
    except common.OperationsNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.OperationsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/operations/items/{row['task_id']}", status_code=303)


@router.post("/items/{task_id}/comments")
async def item_comment(request: Request, task_id: int,
                       principal: Principal = Depends(require_capability("operations.manage"))):
    form = await _form(request)
    try:
        opstasks.add_comment(principal, body=_one(form, "body"), task_id=task_id,
                             actor_user_id=principal.user_id)
    except common.OperationsNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.OperationsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/operations/items/{task_id}", status_code=303)


# --- issues ------------------------------------------------------------------

@router.post("/issues")
async def create_issue(request: Request,
                       principal: Principal = Depends(require_capability("operations.manage"))):
    form = await _form(request)
    try:
        opstasks.add_issue(principal, title=_one(form, "title"),
                           issue_type=_one(form, "issue_type") or "issue",
                           project_id=_int(form, "project_id"), task_id=_int(form, "task_id"),
                           severity=_one(form, "severity") or "medium",
                           description=_one(form, "description") or None,
                           actor_user_id=principal.user_id)
    except common.OperationsNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.OperationsError as exc:
        raise HTTPException(400, str(exc)) from exc
    ref = _int(form, "project_id")
    return RedirectResponse(url=(f"/operations/projects/{ref}" if ref else "/operations"),
                            status_code=303)


@router.post("/issues/{issue_id}/status")
async def issue_status(request: Request, issue_id: int,
                       principal: Principal = Depends(require_capability("operations.manage"))):
    form = await _form(request)
    try:
        opstasks.set_issue_status(principal, issue_id, _one(form, "status"),
                                  actor_user_id=principal.user_id)
    except common.OperationsNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.OperationsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/operations", status_code=303)


# --- templates / resources / capacity ----------------------------------------

@router.post("/templates")
async def create_template(request: Request,
                          principal: Principal = Depends(require_capability("operations.templates"))):
    form = await _form(request)
    try:
        tmpl.create_template(code=_one(form, "code"), name=_one(form, "name"),
                             category=_one(form, "category") or "general",
                             description=_one(form, "description") or None,
                             actor_user_id=principal.user_id)
    except common.OperationsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/operations", status_code=303)


@router.post("/resources")
async def create_resource(request: Request,
                          principal: Principal = Depends(require_capability("operations.templates"))):
    form = await _form(request)
    try:
        tmpl.create_resource(code=_one(form, "code"), name=_one(form, "name"),
                             resource_type=_one(form, "resource_type") or "staff",
                             user_id=_int(form, "user_id"), department=_one(form, "department") or None,
                             role_title=_one(form, "role_title") or None,
                             capacity_minutes_per_day=_int(form, "capacity_minutes_per_day") or 480)
    except common.OperationsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/operations", status_code=303)


@router.post("/capacity")
async def create_capacity(request: Request,
                          principal: Principal = Depends(require_capability("operations.templates"))):
    form = await _form(request)
    try:
        cap.create_capacity_plan(resource_id=_int(form, "resource_id"),
                                 period_start=_date(form, "period_start"),
                                 period_end=_date(form, "period_end"),
                                 planned_minutes=_int(form, "planned_minutes") or 0,
                                 available_minutes=_int(form, "available_minutes") or 0,
                                 actor_user_id=principal.user_id)
    except common.OperationsNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.OperationsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/operations", status_code=303)
