"""Enterprise Integration routes (Phase D.24) — providers, connectors, sync, webhooks, API, events.

New ``/integration`` prefix. It matches no middleware RULE, so each endpoint enforces its
``integration.*`` capability in-route. Managing configuration requires ``integration.manage``;
running syncs / verifying webhooks / recording deliveries / publishing events / connector-status
transitions require ``integration.execute``; audit history requires ``integration.audit``. Secrets
are never returned (credential/webhook secret ciphertext is stripped from responses).
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.integration import api, common, connectors, events, sync, webhooks
from app.services.integration import service as svc
from app.templating import install_filters

router = APIRouter(prefix="/integration", tags=["integration"])
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


# --- overview + reads --------------------------------------------------------

@router.get("", response_class=HTMLResponse)
def overview(request: Request, principal: Principal = Depends(require_capability("integration.view"))):
    return templates.TemplateResponse(request=request, name="integration/overview.html", context={
        "principal": principal, "metrics": svc.overview_metrics(principal),
        "providers": connectors.list_providers(), "connectors": connectors.list_connectors(),
        "can_manage": principal.can("integration.manage"),
        "can_execute": principal.can("integration.execute")})


@router.get("/providers")
def list_providers(request: Request, provider_type: str | None = None,
                   principal: Principal = Depends(require_capability("integration.view"))):
    return JSONResponse({"providers": [
        {"id": p["id"], "code": p["code"], "name": p["name"], "provider_type": p["provider_type"],
         "enabled": p["enabled"]} for p in connectors.list_providers(provider_type=provider_type)]})


@router.get("/connectors")
def list_connectors(request: Request, provider_id: int | None = None, status: str | None = None,
                    principal: Principal = Depends(require_capability("integration.view"))):
    return JSONResponse({"connectors": [
        {"id": c["id"], "code": c["code"], "name": c["name"], "direction": c["direction"],
         "status": c["status"]} for c in connectors.list_connectors(provider_id=provider_id, status=status)]})


@router.get("/connectors/{connector_id}")
def get_connector(request: Request, connector_id: int,
                  principal: Principal = Depends(require_capability("integration.view"))):
    c = connectors.get_connector(principal, connector_id)
    if c is None:
        raise HTTPException(404, "Not found")
    return JSONResponse({"id": c["id"], "code": c["code"], "status": c["status"], "config": c["config"]})


@router.get("/connectors/{connector_id}/audit")
def connector_audit(request: Request, connector_id: int,
                    principal: Principal = Depends(require_capability("integration.audit"))):
    if connectors.get_connector(principal, connector_id) is None:
        raise HTTPException(404, "Not found")
    return JSONResponse({"history": [
        {"event_type": e["event_type"], "to_status": e["to_status"],
         "occurred_at": e["occurred_at"].isoformat()}
        for e in common.audit_history(principal, entity_type="connector", entity_id=connector_id)]})


@router.get("/credentials")
def list_credentials(request: Request, provider_id: int | None = None,
                     principal: Principal = Depends(require_capability("integration.view"))):
    return JSONResponse({"credentials": [
        {"id": r["id"], "code": r["code"], "credential_type": r["credential_type"],
         "reference_kind": r["reference_kind"], "status": r["status"]}
        for r in connectors.list_credentials(provider_id=provider_id)]})   # never includes secrets


@router.get("/sync-profiles")
def list_sync_profiles(request: Request, connector_id: int | None = None,
                       principal: Principal = Depends(require_capability("integration.view"))):
    return JSONResponse({"sync_profiles": [
        {"id": p["id"], "code": p["code"], "name": p["name"], "mapping_version": p["mapping_version"],
         "sync_health": p["sync_health"]} for p in sync.list_sync_profiles(connector_id=connector_id)]})


@router.get("/sync-runs")
def list_sync_runs(request: Request, sync_profile_id: int | None = None, status: str | None = None,
                   page: int = 1, principal: Principal = Depends(require_capability("integration.view"))):
    result = sync.list_sync_runs(sync_profile_id=sync_profile_id, status=status, page=page)
    return JSONResponse({"total": result["total"], "runs": [
        {"id": r["id"], "status": r["status"], "records_written": r["records_written"],
         "trigger_source": r["trigger_source"]} for r in result["rows"]]})


@router.get("/sync-runs/{run_id}")
def get_sync_run(request: Request, run_id: int,
                 principal: Principal = Depends(require_capability("integration.view"))):
    r = sync.get_sync_run(principal, run_id)
    if r is None:
        raise HTTPException(404, "Not found")
    return JSONResponse({"id": r["id"], "status": r["status"], "records_written": r["records_written"],
                         "import_jobs_id": r["import_jobs_id"], "last_error": r["last_error"]})


@router.get("/conflicts")
def list_conflicts(request: Request, sync_run_id: int | None = None, resolution: str | None = None,
                   principal: Principal = Depends(require_capability("integration.view"))):
    return JSONResponse({"conflicts": [
        {"id": c["id"], "entity_type": c["entity_type"], "field_name": c["field_name"],
         "resolution": c["resolution"]} for c in sync.list_conflicts(sync_run_id=sync_run_id, resolution=resolution)]})


@router.get("/webhooks")
def list_webhooks(request: Request, principal: Principal = Depends(require_capability("integration.view"))):
    return JSONResponse({"endpoints": [
        {"id": e["id"], "code": e["code"], "name": e["name"], "direction": e["direction"],
         "verification_status": e["verification_status"]} for e in webhooks.list_endpoints()]})


@router.get("/webhooks/{endpoint_id}/subscriptions")
def list_webhook_subs(request: Request, endpoint_id: int,
                      principal: Principal = Depends(require_capability("integration.view"))):
    return JSONResponse({"subscriptions": [
        {"id": s["id"], "event_type": s["event_type"], "active": s["active"]}
        for s in webhooks.list_subscriptions(endpoint_id=endpoint_id)]})


@router.get("/webhooks/deliveries")
def list_deliveries(request: Request, endpoint_id: int | None = None, status: str | None = None,
                    principal: Principal = Depends(require_capability("integration.view"))):
    result = webhooks.list_deliveries(endpoint_id=endpoint_id, status=status)
    return JSONResponse({"total": result["total"], "deliveries": [
        {"id": d["id"], "event_type": d["event_type"], "status": d["status"], "attempts": d["attempts"]}
        for d in result["rows"]]})


@router.get("/api-clients")
def list_api_clients(request: Request, status: str | None = None,
                     principal: Principal = Depends(require_capability("integration.view"))):
    return JSONResponse({"api_clients": [
        {"id": c["id"], "code": c["code"], "name": c["name"], "status": c["status"],
         "rate_limit_per_minute": c["rate_limit_per_minute"]} for c in api.list_api_clients(status=status)]})


@router.get("/api-clients/{client_id}/usage")
def list_api_usage(request: Request, client_id: int,
                   principal: Principal = Depends(require_capability("integration.view"))):
    return JSONResponse({"usage": [
        {"id": u["id"], "endpoint": u["endpoint"], "request_count": u["request_count"],
         "error_count": u["error_count"]} for u in api.list_usage(api_client_id=client_id)]})


@router.get("/events/definitions")
def list_event_defs(request: Request, principal: Principal = Depends(require_capability("integration.view"))):
    return JSONResponse({"definitions": [
        {"id": d["id"], "code": d["code"], "name": d["name"], "active": d["active"]}
        for d in events.list_definitions()]})


@router.get("/events/subscriptions")
def list_event_subs(request: Request, event_definition_id: int | None = None,
                    principal: Principal = Depends(require_capability("integration.view"))):
    return JSONResponse({"subscriptions": [
        {"id": s["id"], "subscriber": s["subscriber"], "subscriber_type": s["subscriber_type"]}
        for s in events.list_subscriptions(event_definition_id=event_definition_id)]})


@router.get("/data-profiles")
def list_data_profiles(request: Request, profile_type: str | None = None,
                       principal: Principal = Depends(require_capability("integration.view"))):
    return JSONResponse({"data_profiles": [
        {"id": p["id"], "code": p["code"], "name": p["name"], "profile_type": p["profile_type"],
         "data_format": p["data_format"]} for p in connectors.list_data_profiles(profile_type=profile_type)]})


# --- providers / credentials / connectors (manage) ---------------------------

@router.post("/providers")
async def create_provider(request: Request, principal: Principal = Depends(require_capability("integration.manage"))):
    form = await _form(request)
    try:
        p = connectors.create_provider(code=_one(form, "code"), name=_one(form, "name"),
                                       provider_type=_one(form, "provider_type") or "other",
                                       actor_user_id=principal.user_id)
    except common.IntegrationError as exc:
        _err(exc)
    return JSONResponse({"id": p["id"]}, status_code=201)


@router.post("/providers/{provider_id}/enabled")
async def provider_enabled(request: Request, provider_id: int,
                           principal: Principal = Depends(require_capability("integration.manage"))):
    form = await _form(request)
    try:
        p = connectors.set_provider_enabled(principal, provider_id, _one(form, "enabled") == "1",
                                            actor_user_id=principal.user_id)
    except common.IntegrationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    return JSONResponse({"id": p["id"], "enabled": p["enabled"]})


@router.post("/credentials")
async def create_credential(request: Request, principal: Principal = Depends(require_capability("integration.manage"))):
    form = await _form(request)
    try:
        r = connectors.create_credential_reference(
            code=_one(form, "code"), credential_type=_one(form, "credential_type") or "oauth",
            reference_kind=_one(form, "reference_kind") or "microsoft_account",
            reference_id=_int(form, "reference_id"), secret=_one(form, "secret") or None,
            provider_id=_int(form, "provider_id"), actor_user_id=principal.user_id)
    except common.IntegrationError as exc:
        _err(exc)
    return JSONResponse({"id": r["id"], "reference_kind": r["reference_kind"]}, status_code=201)


@router.post("/credentials/{credential_id}/rotate")
async def rotate_credential(request: Request, credential_id: int,
                            principal: Principal = Depends(require_capability("integration.manage"))):
    form = await _form(request)
    try:
        connectors.rotate_credential_reference(principal, credential_id, secret=_one(form, "secret") or None,
                                               reference_id=_int(form, "reference_id"),
                                               actor_user_id=principal.user_id)
    except common.IntegrationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    return JSONResponse({"id": credential_id, "rotated": True})


@router.post("/connectors")
async def create_connector(request: Request, principal: Principal = Depends(require_capability("integration.manage"))):
    form = await _form(request)
    try:
        c = connectors.create_connector(principal, provider_id=_int(form, "provider_id"),
                                        code=_one(form, "code"), name=_one(form, "name"),
                                        direction=_one(form, "direction") or "inbound",
                                        credential_reference_id=_int(form, "credential_reference_id"),
                                        actor_user_id=principal.user_id)
    except common.IntegrationError as exc:
        _err(exc)
    return JSONResponse({"id": c["id"]}, status_code=201)


@router.post("/connectors/{connector_id}/configure")
async def configure_connector(request: Request, connector_id: int,
                              principal: Principal = Depends(require_capability("integration.manage"))):
    form = await _form(request)
    try:
        connectors.configure_connector(principal, connector_id,
                                       credential_reference_id=_int(form, "credential_reference_id"),
                                       actor_user_id=principal.user_id)
    except common.IntegrationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.IntegrationError as exc:
        _err(exc)
    return RedirectResponse(url="/integration", status_code=303)


@router.post("/connectors/{connector_id}/status")
async def connector_status(request: Request, connector_id: int,
                           principal: Principal = Depends(require_capability("integration.execute"))):
    form = await _form(request)
    try:
        c = connectors.set_connector_status(principal, connector_id, _one(form, "status"),
                                            error=_one(form, "error") or None, actor_user_id=principal.user_id)
    except common.IntegrationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.IntegrationError as exc:
        _err(exc)
    return JSONResponse({"id": c["id"], "status": c["status"]})


# --- sync (manage + execute) -------------------------------------------------

@router.post("/sync-profiles")
async def create_sync_profile(request: Request, principal: Principal = Depends(require_capability("integration.manage"))):
    form = await _form(request)
    try:
        p = sync.create_sync_profile(principal, connector_id=_int(form, "connector_id"),
                                     code=_one(form, "code"), name=_one(form, "name"),
                                     direction=_one(form, "direction") or "inbound",
                                     schedule_frequency=_one(form, "schedule_frequency") or None,
                                     actor_user_id=principal.user_id)
    except common.IntegrationError as exc:
        _err(exc)
    return JSONResponse({"id": p["id"]}, status_code=201)


@router.post("/sync-profiles/{profile_id}/mapping")
async def update_mapping(request: Request, profile_id: int,
                         principal: Principal = Depends(require_capability("integration.manage"))):
    form = await _form(request)
    try:
        p = sync.update_mapping(principal, profile_id, {"note": _one(form, "note") or "updated"},
                                actor_user_id=principal.user_id)
    except common.IntegrationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    return JSONResponse({"id": p["id"], "mapping_version": p["mapping_version"]})


@router.post("/sync-profiles/{profile_id}/run")
async def run_sync(request: Request, profile_id: int,
                   principal: Principal = Depends(require_capability("integration.execute"))):
    form = await _form(request)
    try:
        r = sync.run_sync(principal, profile_id, trigger_source="manual",
                          status=_one(form, "status") or "succeeded",
                          records_written=_int(form, "records_written") or 0,
                          person_id=_int(form, "person_id"), actor_user_id=principal.user_id)
    except common.IntegrationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.IntegrationError as exc:
        _err(exc)
    return JSONResponse({"run_id": r["id"], "status": r["status"]})


@router.post("/sync-runs/{run_id}/conflicts")
async def record_conflict(request: Request, run_id: int,
                          principal: Principal = Depends(require_capability("integration.execute"))):
    form = await _form(request)
    try:
        c = sync.record_conflict(principal, run_id, entity_type=_one(form, "entity_type"),
                                 entity_id=_int(form, "entity_id"), field_name=_one(form, "field_name") or None,
                                 actor_user_id=principal.user_id)
    except common.IntegrationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    return JSONResponse({"id": c["id"]}, status_code=201)


@router.post("/conflicts/{conflict_id}/resolve")
async def resolve_conflict(request: Request, conflict_id: int,
                           principal: Principal = Depends(require_capability("integration.manage"))):
    form = await _form(request)
    try:
        c = sync.resolve_conflict(principal, conflict_id, _one(form, "resolution"),
                                  actor_user_id=principal.user_id)
    except common.IntegrationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.IntegrationError as exc:
        _err(exc)
    return JSONResponse({"id": c["id"], "resolution": c["resolution"]})


# --- webhooks (manage + execute) ---------------------------------------------

@router.post("/webhooks")
async def create_endpoint(request: Request, principal: Principal = Depends(require_capability("integration.manage"))):
    form = await _form(request)
    try:
        e = webhooks.create_endpoint(principal, code=_one(form, "code"), name=_one(form, "name"),
                                     direction=_one(form, "direction") or "outbound",
                                     url=_one(form, "url") or None,
                                     signing_algorithm=_one(form, "signing_algorithm") or "hmac_sha256",
                                     signing_secret=_one(form, "signing_secret") or None,
                                     actor_user_id=principal.user_id)
    except common.IntegrationError as exc:
        _err(exc)
    return JSONResponse({"id": e["id"]}, status_code=201)


@router.post("/webhooks/{endpoint_id}/verify")
async def verify_endpoint(request: Request, endpoint_id: int,
                          principal: Principal = Depends(require_capability("integration.execute"))):
    try:
        r = webhooks.verify_endpoint(principal, endpoint_id, actor_user_id=principal.user_id)
    except common.IntegrationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    return JSONResponse(r)


@router.post("/webhooks/{endpoint_id}/subscriptions")
async def create_webhook_sub(request: Request, endpoint_id: int,
                             principal: Principal = Depends(require_capability("integration.manage"))):
    form = await _form(request)
    try:
        s = webhooks.create_subscription(principal, endpoint_id, event_type=_one(form, "event_type"),
                                         actor_user_id=principal.user_id)
    except common.IntegrationError as exc:
        _err(exc)
    return JSONResponse({"id": s["id"]}, status_code=201)


@router.post("/webhooks/deliveries")
async def record_delivery(request: Request, principal: Principal = Depends(require_capability("integration.execute"))):
    form = await _form(request)
    try:
        d = webhooks.record_delivery(principal, event_type=_one(form, "event_type"),
                                     endpoint_id=_int(form, "endpoint_id"),
                                     subscription_id=_int(form, "subscription_id"),
                                     event_id=_one(form, "event_id") or None,
                                     status=_one(form, "status") or "pending", actor_user_id=principal.user_id)
    except common.IntegrationError as exc:
        _err(exc)
    return JSONResponse({"id": d["id"], "status": d["status"]}, status_code=201)


# --- API platform (manage + execute) -----------------------------------------

@router.post("/api-clients")
async def create_api_client(request: Request, principal: Principal = Depends(require_capability("integration.manage"))):
    form = await _form(request)
    try:
        c = api.create_api_client(principal, code=_one(form, "code"), name=_one(form, "name"),
                                  client_type=_one(form, "client_type") or "internal",
                                  rate_limit_per_minute=_int(form, "rate_limit_per_minute"),
                                  rate_limit_per_day=_int(form, "rate_limit_per_day"),
                                  actor_user_id=principal.user_id)
    except common.IntegrationError as exc:
        _err(exc)
    return JSONResponse({"id": c["id"]}, status_code=201)


@router.post("/api-clients/{client_id}/status")
async def api_client_status(request: Request, client_id: int,
                            principal: Principal = Depends(require_capability("integration.manage"))):
    form = await _form(request)
    try:
        c = api.set_api_client_status(principal, client_id, _one(form, "status"), actor_user_id=principal.user_id)
    except common.IntegrationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.IntegrationError as exc:
        _err(exc)
    return JSONResponse({"id": c["id"], "status": c["status"]})


@router.post("/api-clients/{client_id}/usage")
async def record_usage(request: Request, client_id: int,
                       principal: Principal = Depends(require_capability("integration.execute"))):
    form = await _form(request)
    try:
        u = api.record_usage(principal, client_id, endpoint=_one(form, "endpoint") or None,
                             method=_one(form, "method") or None, request_count=_int(form, "request_count") or 0,
                             error_count=_int(form, "error_count") or 0, actor_user_id=principal.user_id)
    except common.IntegrationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    return JSONResponse({"id": u["id"]}, status_code=201)


# --- events (manage + execute) -----------------------------------------------

@router.post("/events/definitions")
async def create_event_def(request: Request, principal: Principal = Depends(require_capability("integration.manage"))):
    form = await _form(request)
    try:
        d = events.create_definition(code=_one(form, "code"), name=_one(form, "name"),
                                     category=_one(form, "category") or None, actor_user_id=principal.user_id)
    except common.IntegrationError as exc:
        _err(exc)
    return JSONResponse({"id": d["id"]}, status_code=201)


@router.post("/events/subscriptions")
async def create_event_sub(request: Request, principal: Principal = Depends(require_capability("integration.manage"))):
    form = await _form(request)
    try:
        s = events.create_subscription(event_definition_id=_int(form, "event_definition_id"),
                                       subscriber=_one(form, "subscriber"),
                                       subscriber_type=_one(form, "subscriber_type") or "internal",
                                       target_id=_int(form, "target_id"), actor_user_id=principal.user_id)
    except common.IntegrationError as exc:
        _err(exc)
    return JSONResponse({"id": s["id"]}, status_code=201)


@router.post("/events/publish")
async def publish_event(request: Request, principal: Principal = Depends(require_capability("integration.execute"))):
    form = await _form(request)
    try:
        event_id = events.publish_event(principal, _one(form, "code"),
                                        subject_ref=_one(form, "subject_ref") or None,
                                        actor_user_id=principal.user_id)
    except common.IntegrationNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    return JSONResponse({"event_id": event_id})


@router.post("/data-profiles")
async def create_data_profile(request: Request, principal: Principal = Depends(require_capability("integration.manage"))):
    form = await _form(request)
    try:
        p = connectors.create_data_profile(code=_one(form, "code"), name=_one(form, "name"),
                                           profile_type=_one(form, "profile_type") or "import",
                                           data_format=_one(form, "data_format") or "csv",
                                           provider_id=_int(form, "provider_id"), actor_user_id=principal.user_id)
    except common.IntegrationError as exc:
        _err(exc)
    return JSONResponse({"id": p["id"]}, status_code=201)
