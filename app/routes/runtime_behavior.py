"""Runtime behavior / adoption routes (Phase D.30) — /runtime/behavior.

New ``/runtime/behavior`` prefix. It matches no middleware RULE, so each endpoint enforces its
``runtime.*`` capability in-route (reusing the D.28 runtime capabilities). Registry + adoption reads
require ``runtime.view``; the behavioral-event history requires ``runtime.audit``; recording a
behavior migrated/retired or migration-completed requires ``runtime.admin``. The runtime engine
remains the sole evaluator; this surface only reports/administers the behavioral-migration registry
and never bypasses RBAC.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.runtime import behavior, governance
from app.services.runtime.coordination_common import as_json
from app.templating import install_filters

router = APIRouter(prefix="/runtime/behavior", tags=["runtime-behavior"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


async def _form(request):
    return parse_qs((await request.body()).decode("utf-8"))


def _one(form, key):
    return (form.get(key, [""])[0]).strip()


@router.get("", response_class=HTMLResponse)
def overview(request: Request, principal: Principal = Depends(require_capability("runtime.view"))):
    gov = governance.validate()
    return templates.TemplateResponse(request=request, name="runtime/behavior.html", context={
        "principal": principal, "adoption": behavior.adoption(principal),
        "behaviors": behavior.list_behaviors(),
        "governance": {"ok": gov["ok"], "issue_count": gov["issue_count"],
                       "coverage_pct": gov["coverage"]["coverage_pct"]},
        "can_admin": principal.can("runtime.admin")})


@router.get("/adoption")
def adoption(request: Request, principal: Principal = Depends(require_capability("runtime.view"))):
    return JSONResponse(as_json(behavior.adoption(principal)))


@router.get("/registry")
def registry(request: Request, status: str | None = None, module: str | None = None,
             principal: Principal = Depends(require_capability("runtime.view"))):
    return JSONResponse({"behaviors": [
        {"code": b["code"], "module": b["module"], "name": b["name"], "status": b["status"],
         "runtime_key": b["runtime_key"], "consumes_config": b["consumes_config"]}
        for b in behavior.list_behaviors(status=status, module=module)]})


@router.get("/events")
def events(request: Request, code: str | None = None,
           principal: Principal = Depends(require_capability("runtime.audit"))):
    return JSONResponse({"events": as_json(behavior.audit_history(principal, code=code))})


@router.post("/{code}/migrate")
def mark_migrated(code: str, request: Request,
                  principal: Principal = Depends(require_capability("runtime.admin"))):
    try:
        row = behavior.mark_migrated(code, actor_user_id=principal.user_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return JSONResponse({"code": row["code"], "status": row["status"]})


@router.post("/{code}/retire")
def mark_retired(code: str, request: Request,
                 principal: Principal = Depends(require_capability("runtime.admin"))):
    try:
        row = behavior.mark_retired(code, actor_user_id=principal.user_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return JSONResponse({"code": row["code"], "status": row["status"]})


@router.post("/migration-completed")
def migration_completed(request: Request,
                        principal: Principal = Depends(require_capability("runtime.admin"))):
    return JSONResponse(as_json(behavior.record_migration_completed(actor_user_id=principal.user_id)))


# --- runtime governance (D.31) -----------------------------------------------

@router.get("/governance")
def governance_report(request: Request,
                      principal: Principal = Depends(require_capability("runtime.audit"))):
    """The runtime-metadata governance report: missing/orphan/deprecated definitions, invalid edition
    mappings, orphan capabilities, and definition coverage for authoritative behaviors."""
    return JSONResponse(as_json(governance.validate()))


@router.post("/governance/validate")
def governance_validate(request: Request,
                        principal: Principal = Depends(require_capability("runtime.admin"))):
    """Run governance validation and record a firm-level governance_validation_completed event."""
    return JSONResponse(as_json(governance.record_validation(actor_user_id=principal.user_id)))
