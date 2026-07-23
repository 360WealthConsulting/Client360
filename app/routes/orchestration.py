"""Workflow Orchestration routes (Phase D.33) — /orchestration.

New ``/orchestration`` prefix. It matches no middleware RULE, so each endpoint enforces its D.17
``workflow.*`` capability in-route (reusing the existing workflow capabilities — no new capabilities,
no RBAC changes). Registry / definition / instance / diagnostics reads require ``workflow.view``; the
governance report, replay, and event history require ``workflow.audit``; simulation requires
``workflow.execute``; running governance validation requires ``workflow.admin``. The engine consumes
``RuntimeContext`` + the policy engine (the runtime engine remains the sole evaluator, the policy
engine the sole decision engine); this surface only reports/administers orchestration and never
bypasses RBAC. Replay and simulation never mutate production state.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.orchestration import diagnostics, engine, governance, registry, replay, simulation
from app.services.orchestration.common import as_json
from app.templating import install_filters

router = APIRouter(prefix="/orchestration", tags=["orchestration"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


@router.get("", response_class=HTMLResponse)
def overview(request: Request, principal: Principal = Depends(require_capability("workflow.view"))):
    gov = governance.validate()
    return templates.TemplateResponse(request=request, name="orchestration/overview.html", context={
        "principal": principal, "adoption": registry.adoption(principal),
        "definitions": registry.list_definitions(), "stats": engine.stats(),
        "governance": {"ok": gov["ok"], "issue_count": gov["issue_count"],
                       "coverage_pct": gov["coverage"].get("coverage_pct")},
        "can_admin": principal.can("workflow.admin")})


@router.get("/registry")
def registry_list(request: Request, status: str | None = None, category: str | None = None,
                  principal: Principal = Depends(require_capability("workflow.view"))):
    return JSONResponse({"definitions": [
        {"code": d["code"], "category": d["category"], "name": d["name"], "status": d["status"],
         "version": d["version"], "owner": d["owner"], "initial_stage": d["initial_stage"],
         "completion_stages": d["completion_stages"], "policy_refs": d["policy_refs"],
         "depends_on": d["depends_on"]} for d in registry.list_definitions(status=status, category=category)]})


@router.get("/adoption")
def adoption(request: Request, principal: Principal = Depends(require_capability("workflow.view"))):
    return JSONResponse(as_json(registry.adoption(principal)))


@router.get("/graph")
def graph(request: Request, principal: Principal = Depends(require_capability("workflow.view"))):
    return JSONResponse({"dependency_graph": registry.dependency_graph()})


@router.get("/governance")
def governance_report(request: Request,
                      principal: Principal = Depends(require_capability("workflow.audit"))):
    """The orchestration governance report: unreachable stages, orphan/circular transitions, duplicate
    ids, missing policy references, missing runtime dependencies, invalid ownership / completion paths."""
    return JSONResponse(as_json(governance.validate()))


@router.post("/governance/validate")
def governance_validate(request: Request,
                        principal: Principal = Depends(require_capability("workflow.admin"))):
    return JSONResponse(as_json(governance.record_validation(actor_user_id=principal.user_id)))


@router.get("/instances")
def instances(request: Request, definition_code: str | None = None, status: str | None = None,
              principal: Principal = Depends(require_capability("workflow.view"))):
    return JSONResponse({"instances": as_json(
        engine.list_instances(definition_code=definition_code, status=status))})


@router.get("/instances/{instance_id}")
def instance_diagnostics(instance_id: int, request: Request,
                         principal: Principal = Depends(require_capability("workflow.view"))):
    diag = diagnostics.diagnostics(instance_id)
    if not diag:
        raise HTTPException(404, f"orchestration instance {instance_id} not found")
    return JSONResponse(as_json(diag))


@router.get("/instances/{instance_id}/replay")
def instance_replay(instance_id: int, request: Request,
                    principal: Principal = Depends(require_capability("workflow.audit"))):
    """Deterministically replay an instance from its recorded events (read-only; never mutates state)."""
    try:
        return JSONResponse(as_json(replay.replay(instance_id)))
    except Exception as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/definitions/{code}")
def definition_detail(code: str, request: Request,
                      principal: Principal = Depends(require_capability("workflow.view"))):
    row = registry.get_definition(code)
    if row is None:
        raise HTTPException(404, f"unknown definition {code!r}")
    return JSONResponse(as_json({**row, "execution_graph": diagnostics.execution_graph(code)}))


@router.get("/definitions/{code}/simulate")
def definition_simulate(code: str, request: Request, subject: str | None = None,
                        principal: Principal = Depends(require_capability("workflow.view"))):
    """Pre-flight simulation of a definition: transition validation, policy verification, dependency
    analysis (all read-only; never mutates production state)."""
    if registry.get_definition(code) is None:
        raise HTTPException(404, f"unknown definition {code!r}")
    return JSONResponse(as_json({
        "transitions": simulation.validate_transitions(code),
        "policies": simulation.verify_policies(code, subject=subject),
        "dependencies": simulation.dependency_analysis(code)}))


@router.post("/simulate")
async def simulate(request: Request,
                   principal: Principal = Depends(require_capability("workflow.execute"))):
    """Dry-run an action sequence against a definition (read-only; never mutates production state).
    Body: ``{definition_code, actions: [...], subject?}``."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    code = (body or {}).get("definition_code")
    actions = (body or {}).get("actions") or []
    if not code:
        raise HTTPException(400, "definition_code is required")
    return JSONResponse(as_json(simulation.dry_run(code, actions, subject=(body or {}).get("subject"))))
