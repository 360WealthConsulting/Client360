from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.security.audit import audit_denied
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import work_queue
from app.services.exception_reporting import dashboard_summary
from app.services.work_management import (
    apply_assignment_rules,
    assign_work,
    authorize_assignment_target,
    authorize_existing_assignment,
    dashboard,
    deactivate_assignment,
    list_assignments,
    queue_detail,
    reassign_work,
)
from app.services.work_queue import dispatch as qdispatch
from app.services.work_queue import views as qv
from app.services.work_queue.diagnostics import work_queue_diagnostics
from app.services.work_queue.governance import validate_work_queue
from app.services.work_queue.summary import work_queue_summary

router = APIRouter(tags=["work-management"])
templates = Jinja2Templates(directory="app/templates")

# Query-string keys the Unified Work Queue reads as filters (bool keys parsed as flags).
_FILTER_KEYS = ("domain", "status", "priority", "sla", "assignee", "team", "person_id",
                "household_id", "search", "due", "due_from", "due_to")
_BOOL_KEYS = ("overdue", "unassigned")


def _query_filters(params) -> dict:
    """Extract the present, non-empty queue filters from a query/form mapping."""
    out = {}
    for k in _FILTER_KEYS:
        v = params.get(k)
        if v not in (None, ""):
            out[k] = v
    for k in _BOOL_KEYS:
        v = params.get(k)
        if str(v).lower() in ("1", "true", "on", "yes"):
            out[k] = True
    return out


class AssignmentCreate(BaseModel):
    entity_type: str; entity_id: int; assignment_role: str
    user_id: int | None = None; team_id: int | None = None
    reason: str | None = None


class Reassignment(BaseModel):
    user_id: int | None = None; team_id: int | None = None
    reason: str | None = None


class AutomaticAssignment(BaseModel):
    entity_type: str; entity_id: int; attributes: dict


def filters(priority=None, status=None, team_id=None, due_before=None, queue=None,
            assignee=None, workflow=None):
    return {"priority": priority, "status": status, "team_id": team_id,
            "due_before": due_before, "queue": queue, "assignee": assignee,
            "workflow": workflow}


@router.get("/work")
def unified_queue(request: Request, view: str | None = None, page: int = 1, page_size: int = 25,
                  principal: Principal = Depends(require_capability("work.read"))):
    """The Unified Work Queue (Phase D.39) — one governed, cross-domain view of actionable work,
    composed read-only from the authoritative task/workflow/exception/compliance/document/tax/
    insurance/opportunity/meeting services. Record scope + capability are always preserved; a tab or
    filter is shown only where the principal has the capability."""
    prefs = qv.get_preferences(principal.user_id)
    view_key = view or prefs["default_view"]
    view_filters = qv.resolve_view(view_key, principal)
    if view_filters is None:
        view_key, view_filters = qv.DEFAULT_VIEW, qv.resolve_view(qv.DEFAULT_VIEW, principal) or {}
    explicit = _query_filters(request.query_params)
    active = {**view_filters, **explicit}
    q = work_queue.compose_queue(principal, filters=active, page=page, page_size=page_size)
    if explicit:
        qv.remember_filters(principal.user_id, explicit)
    return templates.TemplateResponse(request=request, name="work/queue_unified.html", context={
        "principal": principal, "q": q, "view_key": view_key, "active_filters": active,
        "explicit_filters": explicit, "tabs": qv.visible_tabs(principal),
        "saved_views": qv.list_views(principal.user_id), "default_view": prefs["default_view"],
        "last_filters": prefs["last_filters"], "params": request.query_params,
        "can_save_views": principal.can("work_queue.saved_views"),
        "can_act": principal.can("work.write"), "result": request.query_params.get("result"),
        "message": request.query_params.get("msg")})


@router.post("/work/action")
async def work_action(request: Request, principal: Principal = Depends(require_capability("work.read"))):
    """Dispatch ONE action to the authoritative owning service (the queue never mutates directly)."""
    form = await request.form()
    res = qdispatch.dispatch_action(
        principal, work_item_key=form.get("work_item_key"), action=form.get("action"),
        params={"user_id": form.get("user_id"), "note": form.get("note"),
                "resolution_code": form.get("resolution_code")},
        request_id=getattr(request.state, "request_id", None))
    back = form.get("return_to") or "/work"
    sep = "&" if "?" in back else "?"
    return RedirectResponse(f"{back}{sep}result={'ok' if res['ok'] else res['outcome']}"
                            f"&msg={quote(res['message'])}", status_code=303)


@router.post("/work/bulk-action")
async def work_bulk_action(request: Request,
                           principal: Principal = Depends(require_capability("work.read"))):
    """Dispatch one bulk-safe action across selected items — each delegated individually; partial
    success reported honestly."""
    form = await request.form()
    keys = form.getlist("ids")
    res = qdispatch.dispatch_bulk(
        principal, work_item_keys=keys, action=form.get("action"),
        params={"user_id": form.get("user_id")},
        request_id=getattr(request.state, "request_id", None))
    back = form.get("return_to") or "/work"
    sep = "&" if "?" in back else "?"
    msg = quote(f"{res['succeeded']}/{res['total']} succeeded")
    return RedirectResponse(f"{back}{sep}result=bulk&msg={msg}", status_code=303)


@router.post("/work/views")
async def work_save_view(request: Request,
                         principal: Principal = Depends(require_capability("work_queue.saved_views"))):
    """Save or rename a personal saved view (presentation state only)."""
    form = await request.form()
    action = form.get("action")
    if action == "rename" and form.get("view_id"):
        qv.rename_view(principal.user_id, int(form["view_id"]), form.get("name"))
    else:
        qv.save_view(principal.user_id, form.get("name"), _query_filters(form), sort=form.get("sort"))
    return RedirectResponse(url="/work", status_code=303)


@router.post("/work/views/default")
async def work_set_default_view(
        request: Request, principal: Principal = Depends(require_capability("work_queue.saved_views"))):
    """Set (or reset) the user's default queue view."""
    form = await request.form()
    key = form.get("view_key")
    if key:
        qv.set_default(principal.user_id, key)
    else:
        qv.reset_default(principal.user_id)
    return RedirectResponse(url="/work", status_code=303)


@router.post("/work/views/delete")
async def work_delete_view(request: Request,
                           principal: Principal = Depends(require_capability("work_queue.saved_views"))):
    """Delete a user-created saved view."""
    form = await request.form()
    if form.get("view_id"):
        qv.delete_view(principal.user_id, int(form["view_id"]))
    return RedirectResponse(url="/work", status_code=303)


@router.get("/work/summary")
def work_summary(principal: Principal = Depends(require_capability("work.read"))):
    """AI-ready Work Queue Summary (JSON) — counts + top-urgent references + deep links. Read-only."""
    return JSONResponse(work_queue_summary(principal))


@router.get("/work/diagnostics")
def work_diagnostics(principal: Principal = Depends(require_capability("observability.audit"))):
    """Unified Work Queue diagnostics + governance (JSON). Reuses observability.audit."""
    return JSONResponse({"diagnostics": work_queue_diagnostics(principal),
                         "governance": validate_work_queue(principal)})


@router.get("/work/team")
def team_work(request: Request, principal: Principal = Depends(require_capability("capacity.read"))):
    return templates.TemplateResponse(request=request, name="work/team_work.html", context={"work": dashboard(principal), "principal": principal, "exception_summary": dashboard_summary(principal, audience="operations")})


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
def api_my_work(priority: str | None = None, status: str | None = None,
                team_id: int | None = None, due_before: date | None = None,
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
