"""Enterprise Automation routes (Phase D.22) — jobs, schedules, runs, execution.

New ``/automation`` prefix. It matches no middleware RULE, so each endpoint enforces its
``automation.*`` capability in-route; the service enforces record scope on client-anchored runs.
Running/enqueuing jobs requires ``automation.execute`` (the dispatch then executes with system
authority). Sensitive execution history is gated by ``automation.audit``. The runner tick is the
same code the (gated) APScheduler job drives — exposed here for on-demand execution.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.automation import catalog, common, dispatch, runner
from app.services.automation import service as svc
from app.templating import install_filters

router = APIRouter(prefix="/automation", tags=["automation"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


async def _form(request):
    return parse_qs((await request.body()).decode("utf-8"))


def _one(form, key):
    return (form.get(key, [""])[0]).strip()


def _int(form, key):
    v = _one(form, key)
    return int(v) if v else None


# --- overview + reads --------------------------------------------------------

@router.get("", response_class=HTMLResponse)
def overview(request: Request, status: str | None = None, category: str | None = None,
             principal: Principal = Depends(require_capability("automation.view"))):
    return templates.TemplateResponse(request=request, name="automation/overview.html", context={
        "principal": principal, "jobs": svc.list_jobs(principal, status=status, category=category),
        "metrics": svc.metrics(principal), "workers": runner.list_workers(),
        "filters": {"status": status or "", "category": category or ""},
        "can_manage": principal.can("automation.manage"),
        "can_execute": principal.can("automation.execute")})


@router.get("/jobs")
def list_jobs(request: Request, status: str | None = None, category: str | None = None,
              principal: Principal = Depends(require_capability("automation.view"))):
    return JSONResponse({"jobs": [
        {"id": j["id"], "code": j["code"], "name": j["name"], "job_type": j["job_type"],
         "category": j["category"], "status": j["status"]}
        for j in svc.list_jobs(principal, status=status, category=category)]})


@router.get("/job-types")
def list_job_types(request: Request,
                   principal: Principal = Depends(require_capability("automation.view"))):
    return JSONResponse({"job_types": dispatch.list_job_types()})


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int,
               principal: Principal = Depends(require_capability("automation.view"))):
    j = svc.get_job(principal, job_id)
    if j is None:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(request=request, name="automation/job.html", context={
        "principal": principal, "j": j, "can_manage": principal.can("automation.manage"),
        "can_execute": principal.can("automation.execute")})


@router.get("/jobs/{job_id}/audit")
def job_audit(request: Request, job_id: int,
              principal: Principal = Depends(require_capability("automation.audit"))):
    if svc.get_job(principal, job_id) is None:
        raise HTTPException(404, "Not found")
    return JSONResponse({"history": [
        {"event_type": e["event_type"], "to_status": e["to_status"],
         "occurred_at": e["occurred_at"].isoformat()}
        for e in common.audit_history(principal, entity_type="job", entity_id=job_id)]})


@router.get("/runs")
def list_runs(request: Request, job_id: int | None = None, status: str | None = None, page: int = 1,
              principal: Principal = Depends(require_capability("automation.view"))):
    result = svc.list_runs(principal, job_id=job_id, status=status, page=page)
    return JSONResponse({"total": result["total"], "page": result["page"], "runs": [
        {"id": r["id"], "job_id": r["job_id"], "job_type": r["job_type"], "status": r["status"],
         "attempts": r["attempts"], "trigger_source": r["trigger_source"]}
        for r in result["rows"]]})


@router.get("/runs/{run_id}")
def run_detail(request: Request, run_id: int,
               principal: Principal = Depends(require_capability("automation.view"))):
    r = svc.get_run(principal, run_id)
    if r is None:
        raise HTTPException(404, "Not found")
    return JSONResponse({"id": r["id"], "job_type": r["job_type"], "status": r["status"],
                         "attempts": r["attempts"], "max_attempts": r["max_attempts"],
                         "result": r["result"], "last_error": r["last_error"],
                         "duration_ms": r["duration_ms"]})


@router.get("/runs/{run_id}/audit")
def run_audit(request: Request, run_id: int,
              principal: Principal = Depends(require_capability("automation.audit"))):
    try:
        history = svc.run_audit(principal, run_id)
    except common.AutomationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    return JSONResponse({"history": [
        {"event_type": e["event_type"], "to_status": e["to_status"],
         "occurred_at": e["occurred_at"].isoformat()} for e in history]})


@router.get("/schedules")
def list_schedules(request: Request, job_id: int | None = None,
                   principal: Principal = Depends(require_capability("automation.view"))):
    return JSONResponse({"schedules": [
        {"id": s["id"], "job_id": s["job_id"], "name": s["name"], "frequency": s["frequency"],
         "active": s["active"], "next_run_at": s["next_run_at"].isoformat() if s["next_run_at"] else None}
        for s in svc.list_schedules(job_id=job_id)]})


@router.get("/workers")
def list_workers(request: Request,
                 principal: Principal = Depends(require_capability("automation.view"))):
    return JSONResponse({"workers": [
        {"id": w["id"], "code": w["code"], "status": w["status"],
         "last_heartbeat_at": w["last_heartbeat_at"].isoformat() if w["last_heartbeat_at"] else None}
        for w in runner.list_workers()]})


def _catalog_get(fn):
    def handler(request: Request,
                principal: Principal = Depends(require_capability("automation.view"))):
        return JSONResponse({"items": fn()})
    return handler


router.add_api_route("/retry-policies", _catalog_get(catalog.list_retry_policies), methods=["GET"])
router.add_api_route("/failure-policies", _catalog_get(catalog.list_failure_policies), methods=["GET"])
router.add_api_route("/queues", _catalog_get(catalog.list_queues), methods=["GET"])
router.add_api_route("/windows", _catalog_get(catalog.list_windows), methods=["GET"])
router.add_api_route("/templates", _catalog_get(lambda: catalog.list_templates()), methods=["GET"])


# --- jobs (manage) -----------------------------------------------------------

@router.post("/jobs")
async def create_job(request: Request,
                     principal: Principal = Depends(require_capability("automation.manage"))):
    form = await _form(request)
    try:
        j = svc.create_job(principal, code=_one(form, "code"), name=_one(form, "name"),
                           job_type=_one(form, "job_type") or "maintenance",
                           category=_one(form, "category") or "general",
                           description=_one(form, "description") or None,
                           retry_policy_id=_int(form, "retry_policy_id"),
                           failure_policy_id=_int(form, "failure_policy_id"),
                           queue_id=_int(form, "queue_id"), actor_user_id=principal.user_id)
    except common.AutomationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/automation/jobs/{j['id']}", status_code=303)


@router.post("/jobs/{job_id}/status")
async def job_status(request: Request, job_id: int,
                     principal: Principal = Depends(require_capability("automation.manage"))):
    form = await _form(request)
    try:
        svc.set_job_status(principal, job_id, _one(form, "status"), actor_user_id=principal.user_id)
    except common.AutomationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.AutomationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/automation/jobs/{job_id}", status_code=303)


@router.post("/jobs/{job_id}/schedules")
async def create_schedule(request: Request, job_id: int,
                          principal: Principal = Depends(require_capability("automation.manage"))):
    form = await _form(request)
    try:
        svc.create_schedule(principal, job_id, name=_one(form, "name"),
                            schedule_type=_one(form, "schedule_type") or "interval",
                            frequency=_one(form, "frequency") or "manual",
                            interval_seconds=_int(form, "interval_seconds"),
                            actor_user_id=principal.user_id)
    except common.AutomationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.AutomationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/automation/jobs/{job_id}", status_code=303)


@router.post("/schedules/{schedule_id}/active")
async def schedule_active(request: Request, schedule_id: int,
                          principal: Principal = Depends(require_capability("automation.manage"))):
    form = await _form(request)
    try:
        svc.set_schedule_active(principal, schedule_id, _one(form, "active") == "1",
                                actor_user_id=principal.user_id)
    except common.AutomationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    return RedirectResponse(url="/automation", status_code=303)


# --- execution (automation.execute) ------------------------------------------

@router.post("/jobs/{job_id}/run")
async def run_job(request: Request, job_id: int,
                  principal: Principal = Depends(require_capability("automation.execute"))):
    try:
        run = svc.run_job(principal, job_id, trigger_source="manual", actor_user_id=principal.user_id)
    except common.AutomationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.AutomationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"run_id": run["id"], "status": run["status"]})


@router.post("/runs/{run_id}/execute")
async def execute_run(request: Request, run_id: int,
                      principal: Principal = Depends(require_capability("automation.execute"))):
    if svc.get_run(principal, run_id) is None:
        raise HTTPException(404, "Not found")
    run = svc.execute_run(run_id, worker_code=f"manual:{principal.user_id}")
    return JSONResponse({"run_id": run["id"], "status": run["status"]})


@router.post("/tick")
async def tick(request: Request,
               principal: Principal = Depends(require_capability("automation.execute"))):
    return JSONResponse(runner.run_worker_cycle(worker_code=f"manual:{principal.user_id}"))


# --- catalog (manage/templates) ----------------------------------------------

@router.post("/retry-policies")
async def create_retry_policy(request: Request,
                              principal: Principal = Depends(require_capability("automation.manage"))):
    form = await _form(request)
    try:
        catalog.create_retry_policy(code=_one(form, "code"), name=_one(form, "name"),
                                    max_attempts=_int(form, "max_attempts") or 3,
                                    actor_user_id=principal.user_id)
    except common.AutomationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/automation", status_code=303)


@router.post("/failure-policies")
async def create_failure_policy(request: Request,
                                principal: Principal = Depends(require_capability("automation.manage"))):
    form = await _form(request)
    try:
        catalog.create_failure_policy(code=_one(form, "code"), name=_one(form, "name"),
                                      on_failure=_one(form, "on_failure") or "retry",
                                      actor_user_id=principal.user_id)
    except common.AutomationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/automation", status_code=303)


@router.post("/queues")
async def create_queue(request: Request,
                       principal: Principal = Depends(require_capability("automation.manage"))):
    form = await _form(request)
    try:
        catalog.create_queue(code=_one(form, "code"), name=_one(form, "name"),
                             max_concurrency=_int(form, "max_concurrency") or 1,
                             actor_user_id=principal.user_id)
    except common.AutomationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/automation", status_code=303)


@router.post("/windows")
async def create_window(request: Request,
                        principal: Principal = Depends(require_capability("automation.manage"))):
    form = await _form(request)
    try:
        catalog.create_window(code=_one(form, "code"), name=_one(form, "name"),
                              window_type=_one(form, "window_type") or "execution",
                              start_time=_one(form, "start_time") or None,
                              end_time=_one(form, "end_time") or None,
                              actor_user_id=principal.user_id)
    except common.AutomationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/automation", status_code=303)


@router.post("/templates")
async def create_template(request: Request,
                          principal: Principal = Depends(require_capability("automation.manage"))):
    form = await _form(request)
    try:
        catalog.create_template(code=_one(form, "code"), name=_one(form, "name"),
                                job_type=_one(form, "job_type") or "maintenance",
                                category=_one(form, "category") or "general",
                                actor_user_id=principal.user_id)
    except common.AutomationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/automation", status_code=303)
