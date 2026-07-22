"""Enterprise Configuration routes (Phase D.27) — settings, features, editions, preferences.

New ``/configuration`` prefix. It matches no middleware RULE, so each endpoint enforces its
``configuration.*`` capability in-route. Reads/overview require ``configuration.view``; creating/
configuring metadata requires ``configuration.manage``; approving sets/policies/changes, activating
features, assigning editions, and running reviews require ``configuration.execute``; audit history and
sensitive configuration values require ``configuration.audit``. Sensitive configuration item values
stay server-side (only revealed to ``configuration.audit`` holders). Record scope is enforced for
organization-scoped preferences and edition assignments.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.configuration import (
    catalog,
    common,
    editions,
    features,
    platform,
    preferences,
    scans,
)
from app.services.configuration import service as svc
from app.templating import install_filters

router = APIRouter(prefix="/configuration", tags=["configuration"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


async def _form(request):
    return parse_qs((await request.body()).decode("utf-8"))


def _one(form, key):
    return (form.get(key, [""])[0]).strip()


def _int(form, key):
    v = _one(form, key)
    return int(v) if v else None


def _err(exc):
    raise HTTPException(400, str(exc)) from exc


# --- overview ----------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
def overview(request: Request, principal: Principal = Depends(require_capability("configuration.view"))):
    return templates.TemplateResponse(request=request, name="configuration/overview.html", context={
        "principal": principal, "metrics": svc.overview_metrics(principal),
        "categories": catalog.list_categories(), "editions": editions.list_editions(),
        "can_manage": principal.can("configuration.manage"),
        "can_execute": principal.can("configuration.execute")})


@router.get("/overview")
def overview_json(request: Request, principal: Principal = Depends(require_capability("configuration.view"))):
    return JSONResponse({"metrics": svc.overview_metrics(principal)})


# --- categories / sets / items / versions / overrides ------------------------

@router.get("/categories")
def list_categories(request: Request,
                    principal: Principal = Depends(require_capability("configuration.view"))):
    return JSONResponse({"categories": catalog.list_categories()})


@router.post("/categories")
async def create_category(request: Request,
                          principal: Principal = Depends(require_capability("configuration.manage"))):
    form = await _form(request)
    try:
        row = catalog.create_category(principal, code=_one(form, "code"), name=_one(form, "name"),
                                      sort_order=_int(form, "sort_order") or 0, actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"]}, status_code=201)
    except common.ConfigurationError as exc:
        _err(exc)


@router.get("/sets")
def list_sets(request: Request, category_id: int | None = None, status: str | None = None,
              principal: Principal = Depends(require_capability("configuration.view"))):
    return JSONResponse({"sets": [
        {"id": s["id"], "code": s["code"], "name": s["name"], "status": s["status"]}
        for s in catalog.list_sets(category_id=category_id, status=status)]})


@router.post("/sets")
async def create_set(request: Request,
                     principal: Principal = Depends(require_capability("configuration.manage"))):
    form = await _form(request)
    try:
        row = catalog.create_set(principal, code=_one(form, "code"), name=_one(form, "name"),
                                 category_id=_int(form, "category_id"), actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.ConfigurationError as exc:
        _err(exc)


@router.post("/sets/{set_id}/status")
async def set_set_status(set_id: int, request: Request,
                         principal: Principal = Depends(require_capability("configuration.execute"))):
    form = await _form(request)
    try:
        row = catalog.set_set_status(principal, set_id, _one(form, "status"), actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]})
    except common.ConfigurationNotFound:
        raise HTTPException(404, "set not found") from None
    except common.ConfigurationError as exc:
        _err(exc)


@router.get("/items")
def list_items(request: Request, set_id: int | None = None, status: str | None = None,
               principal: Principal = Depends(require_capability("configuration.view"))):
    return JSONResponse({"items": [
        {"id": i["id"], "code": i["code"], "name": i["name"], "value_type": i["value_type"],
         "status": i["status"], "version": i["version"], "sensitive": i["sensitive"]}
        for i in catalog.list_items(principal, set_id=set_id, status=status)]})


@router.get("/items/{item_id}")
def get_item(item_id: int, request: Request,
             principal: Principal = Depends(require_capability("configuration.view"))):
    item = catalog.get_item(principal, item_id)
    if item is None:
        raise HTTPException(404, "item not found")
    return JSONResponse(common.as_json(item))


@router.post("/items")
async def create_item(request: Request,
                      principal: Principal = Depends(require_capability("configuration.manage"))):
    form = await _form(request)
    try:
        row = catalog.create_item(
            principal, set_id=_int(form, "set_id"), code=_one(form, "code"), name=_one(form, "name"),
            value_type=_one(form, "value_type") or "string",
            sensitive=(_one(form, "sensitive") == "true"),
            runtime_setting_reference=_one(form, "runtime_setting_reference") or None,
            actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "version": row["version"]}, status_code=201)
    except common.ConfigurationError as exc:
        _err(exc)


@router.post("/items/{item_id}/status")
async def set_item_status(item_id: int, request: Request,
                          principal: Principal = Depends(require_capability("configuration.execute"))):
    form = await _form(request)
    try:
        row = catalog.set_item_status(principal, item_id, _one(form, "status"), actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]})
    except common.ConfigurationNotFound:
        raise HTTPException(404, "item not found") from None
    except common.ConfigurationError as exc:
        _err(exc)


@router.get("/items/{item_id}/versions")
def list_versions(item_id: int, request: Request,
                  principal: Principal = Depends(require_capability("configuration.view"))):
    return JSONResponse({"versions": common.as_json(catalog.list_versions(configuration_item_id=item_id))})


@router.post("/items/{item_id}/overrides")
async def set_override(item_id: int, request: Request,
                       principal: Principal = Depends(require_capability("configuration.manage"))):
    form = await _form(request)
    try:
        row = catalog.set_environment_override(principal, item_id, _one(form, "environment") or "production",
                                               None, note=_one(form, "note") or None,
                                               actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "environment": row["environment"]}, status_code=201)
    except common.ConfigurationNotFound:
        raise HTTPException(404, "item not found") from None
    except common.ConfigurationError as exc:
        _err(exc)


@router.get("/overrides")
def list_overrides(request: Request, configuration_item_id: int | None = None,
                   principal: Principal = Depends(require_capability("configuration.view"))):
    return JSONResponse({"overrides": common.as_json(
        catalog.list_overrides(configuration_item_id=configuration_item_id))})


# --- preferences (tenant / organization / user) ------------------------------

@router.get("/preferences")
def list_preferences(request: Request, scope: str | None = None, organization_id: int | None = None,
                     user_id: int | None = None,
                     principal: Principal = Depends(require_capability("configuration.view"))):
    return JSONResponse({"preferences": common.as_json(
        preferences.list_preferences(principal, scope=scope, organization_id=organization_id,
                                     user_id=user_id))})


@router.post("/preferences")
async def set_preference(request: Request,
                         principal: Principal = Depends(require_capability("configuration.manage"))):
    form = await _form(request)
    try:
        row = preferences.set_preference(
            principal, scope=_one(form, "scope") or "tenant", preference_key=_one(form, "preference_key"),
            organization_id=_int(form, "organization_id"), user_id=_int(form, "user_id"),
            reference=_one(form, "reference") or None, actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "scope": row["scope"]}, status_code=201)
    except common.ConfigurationError as exc:
        _err(exc)


# --- feature management ------------------------------------------------------

@router.get("/feature-groups")
def list_feature_groups(request: Request,
                        principal: Principal = Depends(require_capability("configuration.view"))):
    return JSONResponse({"feature_groups": features.list_groups()})


@router.post("/feature-groups")
async def create_feature_group(request: Request,
                               principal: Principal = Depends(require_capability("configuration.manage"))):
    form = await _form(request)
    try:
        row = features.create_group(principal, code=_one(form, "code"), name=_one(form, "name"),
                                    actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"]}, status_code=201)
    except common.ConfigurationError as exc:
        _err(exc)


@router.get("/feature-flags")
def list_feature_flags(request: Request, feature_group_id: int | None = None, status: str | None = None,
                       principal: Principal = Depends(require_capability("configuration.view"))):
    return JSONResponse({"feature_flags": [
        {"id": f["id"], "code": f["code"], "name": f["name"], "status": f["status"],
         "enabled": f["enabled"], "rollout_percentage": f["rollout_percentage"]}
        for f in features.list_flags(feature_group_id=feature_group_id, status=status)]})


@router.post("/feature-flags")
async def create_feature_flag(request: Request,
                              principal: Principal = Depends(require_capability("configuration.manage"))):
    form = await _form(request)
    try:
        row = features.create_flag(
            principal, code=_one(form, "code"), name=_one(form, "name"),
            feature_group_id=_int(form, "feature_group_id"),
            rollout_percentage=_int(form, "rollout_percentage") or 0,
            runtime_setting_reference=_one(form, "runtime_setting_reference") or None,
            actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "enabled": row["enabled"]}, status_code=201)
    except common.ConfigurationError as exc:
        _err(exc)


@router.post("/feature-flags/{flag_id}/status")
async def set_flag_status(flag_id: int, request: Request,
                          principal: Principal = Depends(require_capability("configuration.execute"))):
    form = await _form(request)
    try:
        row = features.set_flag_status(principal, flag_id, _one(form, "status"), actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"], "enabled": row["enabled"]})
    except common.ConfigurationNotFound:
        raise HTTPException(404, "feature flag not found") from None
    except common.ConfigurationError as exc:
        _err(exc)


@router.post("/feature-flags/{flag_id}/rollouts")
async def create_rollout(flag_id: int, request: Request,
                         principal: Principal = Depends(require_capability("configuration.manage"))):
    form = await _form(request)
    try:
        row = features.create_rollout(principal, flag_id, stage=_one(form, "stage"),
                                      percentage=_int(form, "percentage") or 0, actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.ConfigurationError as exc:
        _err(exc)


@router.get("/feature-flags/{flag_id}/rollouts")
def list_rollouts(flag_id: int, request: Request,
                  principal: Principal = Depends(require_capability("configuration.view"))):
    return JSONResponse({"rollouts": common.as_json(features.list_rollouts(feature_flag_id=flag_id))})


@router.post("/rollouts/{rollout_id}/status")
async def set_rollout_status(rollout_id: int, request: Request,
                             principal: Principal = Depends(require_capability("configuration.execute"))):
    form = await _form(request)
    try:
        row = features.set_rollout_status(principal, rollout_id, _one(form, "status"),
                                          actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]})
    except common.ConfigurationNotFound:
        raise HTTPException(404, "rollout not found") from None
    except common.ConfigurationError as exc:
        _err(exc)


# --- editions / licensing ----------------------------------------------------

@router.get("/editions")
def list_editions(request: Request, tier: str | None = None, status: str | None = None,
                  principal: Principal = Depends(require_capability("configuration.view"))):
    return JSONResponse({"editions": [
        {"id": e["id"], "code": e["code"], "name": e["name"], "tier": e["tier"], "status": e["status"]}
        for e in editions.list_editions(tier=tier, status=status)]})


@router.post("/editions")
async def create_edition(request: Request,
                         principal: Principal = Depends(require_capability("configuration.manage"))):
    form = await _form(request)
    try:
        row = editions.create_edition(principal, code=_one(form, "code"), name=_one(form, "name"),
                                      tier=_one(form, "tier") or "standard", actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.ConfigurationError as exc:
        _err(exc)


@router.post("/editions/{edition_id}/status")
async def set_edition_status(edition_id: int, request: Request,
                             principal: Principal = Depends(require_capability("configuration.execute"))):
    form = await _form(request)
    try:
        row = editions.set_edition_status(principal, edition_id, _one(form, "status"),
                                          actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]})
    except common.ConfigurationNotFound:
        raise HTTPException(404, "edition not found") from None
    except common.ConfigurationError as exc:
        _err(exc)


@router.get("/editions/{edition_id}/capabilities")
def list_edition_capabilities(edition_id: int, request: Request,
                              principal: Principal = Depends(require_capability("configuration.view"))):
    return JSONResponse({"capabilities": common.as_json(
        editions.list_edition_capabilities(edition_id=edition_id))})


@router.post("/editions/{edition_id}/capabilities")
async def add_edition_capability(edition_id: int, request: Request,
                                 principal: Principal = Depends(require_capability("configuration.manage"))):
    form = await _form(request)
    try:
        row = editions.add_edition_capability(principal, edition_id, _one(form, "capability_code"),
                                              included=(_one(form, "included") != "false"),
                                              actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"]}, status_code=201)
    except common.ConfigurationError as exc:
        _err(exc)


@router.get("/license-policies")
def list_license_policies(request: Request, edition_id: int | None = None,
                          principal: Principal = Depends(require_capability("configuration.view"))):
    return JSONResponse({"license_policies": common.as_json(
        editions.list_license_policies(edition_id=edition_id))})


@router.post("/license-policies")
async def create_license_policy(request: Request,
                                principal: Principal = Depends(require_capability("configuration.manage"))):
    form = await _form(request)
    try:
        row = editions.create_license_policy(
            principal, code=_one(form, "code"), name=_one(form, "name"),
            edition_id=_int(form, "edition_id"), max_users=_int(form, "max_users"),
            max_organizations=_int(form, "max_organizations"), actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.ConfigurationError as exc:
        _err(exc)


@router.get("/edition-assignments")
def list_assignments(request: Request, edition_id: int | None = None, scope: str | None = None,
                     principal: Principal = Depends(require_capability("configuration.view"))):
    return JSONResponse({"assignments": common.as_json(
        editions.list_assignments(edition_id=edition_id, scope=scope))})


@router.post("/edition-assignments")
async def assign_edition(request: Request,
                         principal: Principal = Depends(require_capability("configuration.execute"))):
    form = await _form(request)
    try:
        row = editions.assign_edition(
            principal, edition_id=_int(form, "edition_id"), scope=_one(form, "scope") or "tenant",
            organization_id=_int(form, "organization_id"), license_policy_id=_int(form, "license_policy_id"),
            actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.ConfigurationError as exc:
        _err(exc)


# --- platform options / admin policies / runtime refs / snapshots / changes --

@router.get("/platform-options")
def list_options(request: Request, category: str | None = None,
                 principal: Principal = Depends(require_capability("configuration.view"))):
    return JSONResponse({"platform_options": common.as_json(platform.list_options(category=category))})


@router.post("/platform-options")
async def upsert_option(request: Request,
                        principal: Principal = Depends(require_capability("configuration.manage"))):
    form = await _form(request)
    try:
        row = platform.upsert_option(principal, code=_one(form, "code"), name=_one(form, "name"),
                                     option_type=_one(form, "option_type") or "boolean",
                                     category=_one(form, "category") or None, actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "code": row["code"]}, status_code=201)
    except common.ConfigurationError as exc:
        _err(exc)


@router.get("/administrative-policies")
def list_admin_policies(request: Request, status: str | None = None,
                        principal: Principal = Depends(require_capability("configuration.view"))):
    return JSONResponse({"administrative_policies": common.as_json(platform.list_admin_policies(status=status))})


@router.post("/administrative-policies")
async def create_admin_policy(request: Request,
                              principal: Principal = Depends(require_capability("configuration.manage"))):
    form = await _form(request)
    try:
        row = platform.create_admin_policy(principal, code=_one(form, "code"), name=_one(form, "name"),
                                           policy_type=_one(form, "policy_type") or None,
                                           actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.ConfigurationError as exc:
        _err(exc)


@router.post("/administrative-policies/{policy_id}/status")
async def set_admin_policy_status(policy_id: int, request: Request,
                                  principal: Principal = Depends(require_capability("configuration.execute"))):
    form = await _form(request)
    try:
        row = platform.set_admin_policy_status(principal, policy_id, _one(form, "status"),
                                               actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]})
    except common.ConfigurationNotFound:
        raise HTTPException(404, "administrative policy not found") from None
    except common.ConfigurationError as exc:
        _err(exc)


@router.get("/runtime-references")
def list_runtime_references(request: Request,
                            principal: Principal = Depends(require_capability("configuration.view"))):
    return JSONResponse({"runtime_references": common.as_json(platform.list_runtime_references())})


@router.post("/runtime-references")
async def create_runtime_reference(request: Request,
                                   principal: Principal = Depends(require_capability("configuration.manage"))):
    form = await _form(request)
    try:
        row = platform.create_runtime_reference(
            principal, code=_one(form, "code"), name=_one(form, "name"),
            env_var=_one(form, "env_var") or None, loader_reference=_one(form, "loader_reference") or None,
            value_type=_one(form, "value_type") or "string", actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"]}, status_code=201)
    except common.ConfigurationError as exc:
        _err(exc)


@router.get("/snapshots")
def list_snapshots(request: Request,
                   principal: Principal = Depends(require_capability("configuration.view"))):
    return JSONResponse({"snapshots": common.as_json(platform.list_snapshots())})


@router.post("/snapshots")
def capture_snapshot(request: Request,
                     principal: Principal = Depends(require_capability("configuration.execute"))):
    row = platform.capture_snapshot(principal, actor_user_id=principal.user_id)
    return JSONResponse({"id": row["id"], "summary": row["summary"]}, status_code=201)


@router.get("/changes")
def list_changes(request: Request, status: str | None = None,
                 principal: Principal = Depends(require_capability("configuration.view"))):
    return JSONResponse({"changes": common.as_json(platform.list_changes(status=status))})


@router.post("/changes")
async def propose_change(request: Request,
                         principal: Principal = Depends(require_capability("configuration.manage"))):
    form = await _form(request)
    try:
        row = platform.propose_change(
            principal, entity_type=_one(form, "entity_type"), entity_id=_int(form, "entity_id"),
            change_type=_one(form, "change_type") or "update", note=_one(form, "note") or None,
            actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.ConfigurationError as exc:
        _err(exc)


@router.post("/changes/{change_id}/decision")
async def decide_change(change_id: int, request: Request,
                        principal: Principal = Depends(require_capability("configuration.execute"))):
    form = await _form(request)
    try:
        row = platform.decide_change(principal, change_id, _one(form, "status"), actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]})
    except common.ConfigurationNotFound:
        raise HTTPException(404, "change not found") from None
    except common.ConfigurationError as exc:
        _err(exc)


# --- reviews (automation-style, manual trigger) ------------------------------

@router.post("/reviews/run")
def run_reviews(request: Request,
                principal: Principal = Depends(require_capability("configuration.execute"))):
    return JSONResponse(scans.run_due_reviews(principal, actor_user_id=principal.user_id))


# --- audit history -----------------------------------------------------------

@router.get("/audit/{entity_type}/{entity_id}")
def audit_history(entity_type: str, entity_id: int, request: Request,
                  principal: Principal = Depends(require_capability("configuration.audit"))):
    return JSONResponse({"events": common.as_json(
        svc.audit_history(principal, entity_type=entity_type, entity_id=entity_id))})
