"""Enterprise Runtime Configuration Engine routes (Phase D.28).

New ``/runtime`` prefix. It matches no middleware RULE, so each endpoint enforces its ``runtime.*``
capability in-route. Reads/overview/effective-config/feature evaluation require ``runtime.view``;
cache warm-up requires ``runtime.manage``; refresh / snapshot build / cache rebuild require
``runtime.execute``; the safety-validation report and audit history require ``runtime.audit``;
emergency configuration overrides require ``runtime.admin``. The engine only evaluates — it never
edits D.27 metadata — and every surface goes through RBAC (runtime evaluation never bypasses
capabilities/scope). The per-request immutable context is exposed via ``current_runtime_context``.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.runtime import common, editions, engine, safety, snapshots
from app.services.runtime import service as svc
from app.services.runtime.middleware import resolve_request_context
from app.templating import install_filters

router = APIRouter(prefix="/runtime", tags=["runtime"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


async def _form(request):
    return parse_qs((await request.body()).decode("utf-8"))


def _one(form, key):
    return (form.get(key, [""])[0]).strip()


def current_runtime_context(request: Request):
    """Dependency: the immutable per-request runtime context (built once, cached on request.state)."""
    return resolve_request_context(request)


# --- overview / readiness ----------------------------------------------------

@router.get("", response_class=HTMLResponse)
def overview(request: Request, principal: Principal = Depends(require_capability("runtime.view"))):
    return templates.TemplateResponse(request=request, name="runtime/overview.html", context={
        "principal": principal, "metrics": svc.overview_metrics(principal),
        "snapshots": snapshots.list_snapshots(limit=10),
        "can_manage": principal.can("runtime.manage"),
        "can_execute": principal.can("runtime.execute")})


@router.get("/overview")
def overview_json(request: Request, principal: Principal = Depends(require_capability("runtime.view"))):
    return JSONResponse({"metrics": svc.overview_metrics(principal)})


@router.get("/readiness")
def readiness(request: Request, principal: Principal = Depends(require_capability("runtime.view"))):
    return JSONResponse(common.as_json(engine.readiness()))


@router.get("/context")
def request_context(request: Request, ctx=Depends(current_runtime_context),
                    principal: Principal = Depends(require_capability("runtime.view"))):
    """The immutable runtime context resolved for THIS request (no repeated resolution)."""
    return JSONResponse(common.as_json(ctx.to_dict()))


# --- effective configuration + evaluation ------------------------------------

@router.get("/effective-config")
def effective_config(request: Request, environment: str = "production",
                     organization_id: int | None = None, user_id: int | None = None,
                     principal: Principal = Depends(require_capability("runtime.view"))):
    return JSONResponse({"effective_config": common.as_json(
        engine.effective_config(principal, environment=environment, organization_id=organization_id,
                                user_id=user_id))})


@router.get("/features")
def evaluate_features(request: Request, organization_id: int | None = None, user_id: int | None = None,
                      principal: Principal = Depends(require_capability("runtime.view"))):
    return JSONResponse({"features": common.as_json(
        engine.evaluate_features(principal, organization_id=organization_id, user_id=user_id))})


@router.get("/features/{code}")
def evaluate_feature(code: str, request: Request, organization_id: int | None = None,
                     user_id: int | None = None,
                     principal: Principal = Depends(require_capability("runtime.view"))):
    result = engine.evaluate_features(principal, organization_id=organization_id, user_id=user_id)
    if code not in result:
        raise HTTPException(404, "feature not found")
    return JSONResponse({"code": code, **common.as_json(result[code])})


@router.get("/edition")
def resolve_edition(request: Request, organization_id: int | None = None,
                    principal: Principal = Depends(require_capability("runtime.view"))):
    ed = editions.resolve_edition(organization_id=organization_id)
    if ed is None:
        return JSONResponse({"edition": None})
    return JSONResponse({"edition": {"code": ed["code"], "tier": ed["tier"], "status": ed["status"]},
                         "capabilities": sorted(editions.edition_capabilities(ed["id"]))})


# --- snapshots ---------------------------------------------------------------

@router.get("/snapshots")
def list_snapshots(request: Request, principal: Principal = Depends(require_capability("runtime.view"))):
    return JSONResponse({"snapshots": [
        {"snapshot_uid": s["snapshot_uid"], "scope": s["scope"], "version": s["version"],
         "config_hash": s["config_hash"], "edition_code": s["edition_code"],
         "item_count": s["item_count"], "feature_count": s["feature_count"]}
        for s in snapshots.list_snapshots()]})


@router.get("/snapshots/current")
def current_snapshot(request: Request, principal: Principal = Depends(require_capability("runtime.view"))):
    snap = snapshots.current_snapshot()
    if snap is None:
        return JSONResponse({"snapshot": None})
    return JSONResponse({"snapshot": common.as_json(snap)})


@router.get("/snapshots/compare")
def compare_snapshots(request: Request, a: str, b: str,
                      principal: Principal = Depends(require_capability("runtime.view"))):
    return JSONResponse(common.as_json(snapshots.compare_snapshots(a, b)))


@router.get("/snapshots/{snapshot_uid}")
def get_snapshot(snapshot_uid: str, request: Request,
                 principal: Principal = Depends(require_capability("runtime.view"))):
    snap = snapshots.get_snapshot(snapshot_uid)
    if snap is None:
        raise HTTPException(404, "snapshot not found")
    return JSONResponse({"snapshot": common.as_json(snap)})


@router.post("/snapshots")
async def build_snapshot(request: Request,
                         principal: Principal = Depends(require_capability("runtime.execute"))):
    form = await _form(request)
    scope = _one(form, "scope") or "manual"
    if scope not in ("manual", "refresh", "scheduler", "background"):
        raise HTTPException(400, "invalid snapshot scope")
    snap = snapshots.build_snapshot(principal, scope=scope, source="api", actor_user_id=principal.user_id)
    return JSONResponse({"snapshot_uid": snap["snapshot_uid"], "version": snap["version"]}, status_code=201)


# --- refresh / cache ---------------------------------------------------------

@router.post("/refresh")
def refresh(request: Request, principal: Principal = Depends(require_capability("runtime.execute"))):
    return JSONResponse(common.as_json(engine.refresh(principal, actor_user_id=principal.user_id)))


@router.get("/cache")
def cache_stats(request: Request, principal: Principal = Depends(require_capability("runtime.view"))):
    from app.services.runtime.cache import RUNTIME_CACHE
    return JSONResponse(common.as_json(RUNTIME_CACHE.stats()))


@router.post("/cache/warm-up")
def warm_up(request: Request, principal: Principal = Depends(require_capability("runtime.manage"))):
    return JSONResponse(common.as_json(engine.warm_up(actor_user_id=principal.user_id)))


# --- safety validation -------------------------------------------------------

@router.get("/validate")
def validate(request: Request, environment: str = "production",
             principal: Principal = Depends(require_capability("runtime.audit"))):
    return JSONResponse(common.as_json(safety.validate(environment=environment)))


# --- emergency overrides (break-glass; admin only) ---------------------------

@router.get("/emergency-overrides")
def list_emergency_overrides(request: Request,
                             principal: Principal = Depends(require_capability("runtime.admin"))):
    return JSONResponse({"emergency_overrides": common.as_json(engine.emergency_overrides())})


@router.post("/emergency-overrides")
async def set_emergency_override(request: Request,
                                 principal: Principal = Depends(require_capability("runtime.admin"))):
    form = await _form(request)
    key = _one(form, "key")
    if not key:
        raise HTTPException(400, "key is required")
    return JSONResponse(common.as_json(engine.set_emergency_override(
        key, _one(form, "value") or None, actor_user_id=principal.user_id)))


@router.post("/emergency-overrides/clear")
async def clear_emergency_override(request: Request,
                                   principal: Principal = Depends(require_capability("runtime.admin"))):
    form = await _form(request)
    key = _one(form, "key")
    if not key:
        raise HTTPException(400, "key is required")
    return JSONResponse(common.as_json(engine.clear_emergency_override(
        key, actor_user_id=principal.user_id)))


# --- audit history -----------------------------------------------------------

@router.get("/audit/{entity_type}/{entity_id}")
def audit_history(entity_type: str, entity_id: int, request: Request,
                  principal: Principal = Depends(require_capability("runtime.audit"))):
    return JSONResponse({"events": common.as_json(
        svc.audit_history(principal, entity_type=entity_type, entity_id=entity_id))})
