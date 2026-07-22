"""Distributed runtime cluster routes (Phase D.29) — /runtime/cluster.

New ``/runtime/cluster`` prefix. It matches no middleware RULE, so each endpoint enforces its
``runtime.*`` capability in-route (reusing the D.28 runtime capabilities). Overview / workers /
versions / convergence require ``runtime.view``; coordinated refresh requires ``runtime.execute``;
diagnostics / event history require ``runtime.audit``; worker administration (expire a worker) and
emergency synchronization require ``runtime.admin``. The runtime engine remains the sole evaluator
and the transactional outbox the sole coordination bus; every surface goes through RBAC.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.runtime import cluster, coordination, generations
from app.services.runtime.coordination_common import as_json
from app.templating import install_filters

router = APIRouter(prefix="/runtime/cluster", tags=["runtime-cluster"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


async def _form(request):
    return parse_qs((await request.body()).decode("utf-8"))


def _one(form, key):
    return (form.get(key, [""])[0]).strip()


# --- overview ----------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
def overview(request: Request, principal: Principal = Depends(require_capability("runtime.view"))):
    return templates.TemplateResponse(request=request, name="runtime/cluster.html", context={
        "principal": principal, "metrics": cluster.overview_metrics(principal),
        "workers": coordination.list_workers(),
        "can_execute": principal.can("runtime.execute"),
        "can_admin": principal.can("runtime.admin")})


@router.get("/overview")
def overview_json(request: Request, principal: Principal = Depends(require_capability("runtime.view"))):
    return JSONResponse(as_json(cluster.overview_metrics(principal)))


# --- workers / versions / convergence ----------------------------------------

@router.get("/workers")
def list_workers(request: Request, status: str | None = None,
                 principal: Principal = Depends(require_capability("runtime.view"))):
    return JSONResponse({"workers": [
        {"worker_uid": w["worker_uid"], "status": w["status"], "health_status": w["health_status"],
         "runtime_version": w["runtime_version"], "snapshot_version": w["snapshot_version"],
         "last_heartbeat_at": w["last_heartbeat_at"].isoformat() if w.get("last_heartbeat_at") else None}
        for w in coordination.list_workers(status=status)]})


@router.get("/versions")
def list_versions(request: Request, principal: Principal = Depends(require_capability("runtime.view"))):
    return JSONResponse({"generations": [
        {"version": g["version"], "generation_uid": g["generation_uid"], "config_hash": g["config_hash"],
         "trigger": g["trigger"], "status": g["status"], "propagation_status": g["propagation_status"],
         "converged_worker_count": g["converged_worker_count"],
         "worker_count_at_activation": g["worker_count_at_activation"]}
        for g in generations.list_generations()]})


@router.get("/convergence")
def convergence(request: Request, principal: Principal = Depends(require_capability("runtime.view"))):
    return JSONResponse(as_json(coordination.convergence()))


# --- coordinated refresh -----------------------------------------------------

@router.post("/refresh")
async def coordinated_refresh(request: Request,
                              principal: Principal = Depends(require_capability("runtime.execute"))):
    form = await _form(request)
    trigger = _one(form, "trigger") or "manual"
    if trigger not in ("manual", "scheduled", "metadata_change"):
        trigger = "manual"
    return JSONResponse(as_json(cluster.coordinated_refresh(
        principal, trigger=trigger, actor_user_id=principal.user_id)))


# --- diagnostics / event history ---------------------------------------------

@router.get("/diagnostics")
def diagnostics(request: Request, principal: Principal = Depends(require_capability("runtime.audit"))):
    return JSONResponse(as_json(cluster.diagnostics(principal)))


@router.get("/events")
def event_history(request: Request, entity_type: str | None = None, event_type: str | None = None,
                  principal: Principal = Depends(require_capability("runtime.audit"))):
    return JSONResponse({"events": as_json(
        cluster.event_history(principal, entity_type=entity_type, event_type=event_type))})


# --- worker administration / emergency synchronization (admin only) ----------

@router.post("/workers/expire")
def expire_stale_workers(request: Request,
                         principal: Principal = Depends(require_capability("runtime.admin"))):
    return JSONResponse(as_json(coordination.expire_stale_workers(actor_user_id=principal.user_id)))


@router.post("/emergency-sync")
def emergency_sync(request: Request,
                   principal: Principal = Depends(require_capability("runtime.admin"))):
    return JSONResponse(as_json(cluster.emergency_synchronization(
        principal, actor_user_id=principal.user_id)))
