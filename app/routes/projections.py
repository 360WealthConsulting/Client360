"""Read Model / Projection routes (Phase D.36) — /projections.

New ``/projections`` prefix. It matches no middleware RULE, so each endpoint enforces its D.26
``observability.*`` capability in-route (reusing the existing observability capabilities — no new
capabilities, no RBAC changes). Registry / diagnostics / health reads require ``observability.view``;
the governance report + full diagnostics require ``observability.audit``; rebuild / reset / replay
require ``observability.execute``. The projection engine only consumes the outbox and writes disposable
read models — this surface never mutates authoritative state and never bypasses RBAC.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.projections import diagnostics, engine, governance, registry
from app.services.projections.common import as_json
from app.templating import install_filters

router = APIRouter(prefix="/projections", tags=["projections"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


async def _body(request):
    try:
        return await request.json()
    except Exception:
        return {}


@router.get("", response_class=HTMLResponse)
def overview(request: Request, principal: Principal = Depends(require_capability("observability.view"))):
    gov = governance.validate()
    return templates.TemplateResponse(request=request, name="projections/overview.html", context={
        "principal": principal, "adoption": registry.adoption(principal),
        "projections": registry.list_definitions(), "health": diagnostics.health(),
        "governance": {"ok": gov["ok"], "issue_count": gov["issue_count"],
                       "coverage_pct": gov["coverage"].get("coverage_pct")},
        "can_admin": principal.can("observability.execute")})


@router.get("/health")
def health(request: Request, principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse(as_json(diagnostics.health()))


@router.get("/diagnostics")
def all_diagnostics(request: Request,
                    principal: Principal = Depends(require_capability("observability.audit"))):
    return JSONResponse(as_json({"adoption": registry.adoption(principal),
                                 "largest": diagnostics.largest_projection(),
                                 "projections": [diagnostics.diagnostics(d["projection_id"])
                                                 for d in registry.list_definitions()]}))


@router.get("/governance")
def governance_report(request: Request,
                      principal: Principal = Depends(require_capability("observability.audit"))):
    """The projection-governance report: missing owner/subscriber, subscriber-without-projection,
    schema/version drift, lag, replay mismatch, dependency cycles, duplicates, reading authoritative."""
    return JSONResponse(as_json(governance.validate()))


@router.post("/governance/validate")
def governance_validate(request: Request,
                        principal: Principal = Depends(require_capability("observability.execute"))):
    return JSONResponse(as_json(governance.record_validation(actor_user_id=principal.user_id)))


@router.post("/rebuild")
async def rebuild(request: Request,
                  principal: Principal = Depends(require_capability("observability.execute"))):
    """Fully rebuild a projection from events (disposable read model; deterministic). Body: {projection_id}."""
    pid = (await _body(request)).get("projection_id")
    if registry.get_definition(pid) is None:
        raise HTTPException(404, f"unknown projection {pid!r}")
    return JSONResponse(as_json(engine.rebuild(pid)))


@router.post("/reset")
async def reset(request: Request,
                principal: Principal = Depends(require_capability("observability.execute"))):
    pid = (await _body(request)).get("projection_id")
    if registry.get_definition(pid) is None:
        raise HTTPException(404, f"unknown projection {pid!r}")
    return JSONResponse(as_json(engine.reset(pid)))


@router.post("/replay")
async def replay(request: Request,
                 principal: Principal = Depends(require_capability("observability.execute"))):
    pid = (await _body(request)).get("projection_id")
    if registry.get_definition(pid) is None:
        raise HTTPException(404, f"unknown projection {pid!r}")
    return JSONResponse(as_json(engine.replay(pid)))


@router.get("/{projection_id}")
def projection_detail(projection_id: str, request: Request,
                      principal: Principal = Depends(require_capability("observability.view"))):
    diag = diagnostics.diagnostics(projection_id)
    if not diag:
        raise HTTPException(404, f"unknown projection {projection_id!r}")
    return JSONResponse(as_json(diag))
