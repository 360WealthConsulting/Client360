"""Runtime Policy routes (Phase D.32) — /runtime/policy.

New ``/runtime/policy`` prefix. It matches no middleware RULE, so each endpoint enforces its
``runtime.*`` capability in-route (reusing the D.28 runtime capabilities — no new capabilities, no RBAC
changes). Registry + dependency-graph reads require ``runtime.view``; the governance report + policy
diagnostics + lifecycle-event history require ``runtime.audit``; running governance validation requires
``runtime.admin``. The Runtime Policy Engine consumes ``RuntimeContext`` (the runtime engine remains the
sole evaluator); this surface only reports/administers the policy registry and never bypasses RBAC.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.policy import engine as policy_engine
from app.services.policy import governance, registry
from app.services.runtime.coordination_common import as_json
from app.templating import install_filters

router = APIRouter(prefix="/runtime/policy", tags=["runtime-policy"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


@router.get("", response_class=HTMLResponse)
def overview(request: Request, principal: Principal = Depends(require_capability("runtime.view"))):
    gov = governance.validate()
    return templates.TemplateResponse(request=request, name="runtime/policy.html", context={
        "principal": principal, "adoption": registry.adoption(principal),
        "policies": registry.list_policies(),
        "governance": {"ok": gov["ok"], "issue_count": gov["issue_count"],
                       "coverage_pct": gov["coverage"].get("coverage_pct"),
                       "definition_coverage_pct": gov["coverage"].get("definition_coverage_pct")},
        "stats": policy_engine.evaluation_stats(),
        "can_admin": principal.can("runtime.admin")})


@router.get("/registry")
def registry_list(request: Request, status: str | None = None, category: str | None = None,
                  principal: Principal = Depends(require_capability("runtime.view"))):
    return JSONResponse({"policies": [
        {"code": p["code"], "category": p["category"], "name": p["name"], "status": p["status"],
         "version": p["version"], "owner": p["owner"], "consumes_feature": p["consumes_feature"],
         "consumes_config": p["consumes_config"], "depends_on": p["depends_on"],
         "in_domain": p["in_domain"], "per_instance": p["per_instance"]}
        for p in registry.list_policies(status=status, category=category)]})


@router.get("/adoption")
def adoption(request: Request, principal: Principal = Depends(require_capability("runtime.view"))):
    return JSONResponse(as_json(registry.adoption(principal)))


@router.get("/graph")
def graph(request: Request, principal: Principal = Depends(require_capability("runtime.view"))):
    return JSONResponse({"dependency_graph": registry.dependency_graph()})


@router.get("/governance")
def governance_report(request: Request,
                      principal: Principal = Depends(require_capability("runtime.audit"))):
    """The policy-registry governance report: duplicate/unreachable/orphan policies, circular
    dependencies, missing runtime definitions, deprecated references, invalid capability references."""
    return JSONResponse(as_json(governance.validate()))


@router.post("/governance/validate")
def governance_validate(request: Request,
                        principal: Principal = Depends(require_capability("runtime.admin"))):
    """Run policy governance validation and record a firm-level policy_governance_validated event."""
    return JSONResponse(as_json(governance.record_validation(actor_user_id=principal.user_id)))


@router.get("/events")
def events(request: Request, code: str | None = None,
           principal: Principal = Depends(require_capability("runtime.audit"))):
    return JSONResponse({"events": as_json(registry.audit_history(principal, code=code))})


@router.post("/registry-updated")
def registry_updated(request: Request,
                     principal: Principal = Depends(require_capability("runtime.admin"))):
    return JSONResponse(as_json(registry.record_registry_updated(actor_user_id=principal.user_id)))


@router.get("/{code}")
def policy_detail(code: str, request: Request,
                  principal: Principal = Depends(require_capability("runtime.view"))):
    row = registry.get_policy(code)
    if row is None:
        raise HTTPException(404, f"unknown policy {code!r}")
    return JSONResponse(as_json(row))


@router.get("/{code}/explain")
def policy_explain(code: str, request: Request, subject: str | None = None,
                   principal: Principal = Depends(require_capability("runtime.audit"))):
    """Diagnostics: evaluate the policy and return its full deterministic explanation (decision,
    runtime snapshot, evaluated features/capabilities). Never mutates."""
    if registry.get_policy(code) is None:
        raise HTTPException(404, f"unknown policy {code!r}")
    return JSONResponse(as_json(policy_engine.explain(code, subject=subject)))
