"""Enterprise Observability routes (Phase D.26) — services, health, diagnostics, telemetry, alerts.

New ``/observability`` prefix. It matches no middleware RULE, so each endpoint enforces its
``observability.*`` capability in-route. Reads/overview require ``observability.view``; creating/
configuring metadata requires ``observability.manage``; running scans / recording snapshots &
results / collecting telemetry / raising & acknowledging & resolving alerts / service-status &
maintenance transitions / incident lifecycle require ``observability.execute``; audit history and
sensitive diagnostic detail require ``observability.audit``. Sensitive diagnostic detail stays
server-side (only exposed to ``observability.audit`` holders).
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.observability import (
    alerts,
    catalog,
    common,
    health,
    incidents,
    scans,
    telemetry,
)
from app.services.observability import service as svc
from app.templating import install_filters

router = APIRouter(prefix="/observability", tags=["observability"])
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
def overview(request: Request, principal: Principal = Depends(require_capability("observability.view"))):
    return templates.TemplateResponse(request=request, name="observability/overview.html", context={
        "principal": principal, "metrics": svc.overview_metrics(principal),
        "services": catalog.list_services(), "alerts": alerts.list_alerts(status="open")["rows"],
        "can_manage": principal.can("observability.manage"),
        "can_execute": principal.can("observability.execute")})


@router.get("/overview")
def overview_json(request: Request, principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse({"metrics": svc.overview_metrics(principal)})


# --- services + dependencies -------------------------------------------------

@router.get("/services")
def list_services(request: Request, service_type: str | None = None, status: str | None = None,
                  principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse({"services": [
        {"id": s["id"], "code": s["code"], "name": s["name"], "service_type": s["service_type"],
         "status": s["status"], "criticality": s["criticality"]}
        for s in catalog.list_services(service_type=service_type, status=status)]})


@router.get("/services/{service_id}")
def get_service(service_id: int, request: Request,
                principal: Principal = Depends(require_capability("observability.view"))):
    svc_row = catalog.get_service(principal, service_id)
    if svc_row is None:
        raise HTTPException(404, "service not found")
    return JSONResponse(common.as_json(svc_row))


@router.post("/services")
async def create_service(request: Request,
                         principal: Principal = Depends(require_capability("observability.manage"))):
    form = await _form(request)
    try:
        row = catalog.create_service(
            principal, code=_one(form, "code"), name=_one(form, "name"),
            service_type=_one(form, "service_type") or "application",
            criticality=_one(form, "criticality") or "medium",
            reference_type=_one(form, "reference_type") or None, reference_id=_int(form, "reference_id"),
            actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.ObservabilityError as exc:
        _err(exc)


@router.post("/services/{service_id}/status")
async def set_service_status(service_id: int, request: Request,
                             principal: Principal = Depends(require_capability("observability.execute"))):
    form = await _form(request)
    try:
        row = catalog.set_service_status(principal, service_id, _one(form, "status"),
                                         detail=_one(form, "detail") or None, actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]})
    except common.ObservabilityNotFound:
        raise HTTPException(404, "service not found") from None
    except common.ObservabilityError as exc:
        _err(exc)


@router.get("/services/{service_id}/dependencies")
def list_dependencies(service_id: int, request: Request,
                      principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse({"dependencies": catalog.list_dependencies(service_id=service_id)})


@router.post("/services/{service_id}/dependencies")
async def add_dependency(service_id: int, request: Request,
                         principal: Principal = Depends(require_capability("observability.manage"))):
    form = await _form(request)
    try:
        row = catalog.add_dependency(principal, service_id, _int(form, "depends_on_service_id"),
                                     dependency_type=_one(form, "dependency_type") or "hard",
                                     actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"]}, status_code=201)
    except common.ObservabilityError as exc:
        _err(exc)


# --- environment / deployment ------------------------------------------------

@router.get("/environments")
def list_environments(request: Request,
                      principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse({"environments": catalog.list_environment_profiles()})


@router.post("/environments")
async def create_environment(request: Request,
                             principal: Principal = Depends(require_capability("observability.manage"))):
    form = await _form(request)
    try:
        row = catalog.create_environment_profile(
            principal, code=_one(form, "code"), name=_one(form, "name"),
            environment=_one(form, "environment") or "production", actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"]}, status_code=201)
    except common.ObservabilityError as exc:
        _err(exc)


@router.get("/deployments")
def list_deployments(request: Request,
                     principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse({"deployments": catalog.list_deployment_references()})


@router.post("/deployments")
async def create_deployment(request: Request,
                            principal: Principal = Depends(require_capability("observability.manage"))):
    form = await _form(request)
    try:
        row = catalog.create_deployment_reference(
            principal, code=_one(form, "code"), version=_one(form, "version"),
            migration_head=_one(form, "migration_head") or None, actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"]}, status_code=201)
    except common.ObservabilityError as exc:
        _err(exc)


# --- health checks + snapshots -----------------------------------------------

@router.get("/health-checks")
def list_health_checks(request: Request, service_id: int | None = None,
                       principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse({"health_checks": [
        {"id": h["id"], "code": h["code"], "name": h["name"], "check_type": h["check_type"],
         "last_status": h["last_status"]} for h in health.list_health_checks(service_id=service_id)]})


@router.post("/health-checks")
async def create_health_check(request: Request,
                              principal: Principal = Depends(require_capability("observability.manage"))):
    form = await _form(request)
    try:
        row = health.create_health_check(
            principal, code=_one(form, "code"), name=_one(form, "name"),
            service_id=_int(form, "service_id"), check_type=_one(form, "check_type") or "liveness",
            target_reference=_one(form, "target_reference") or None,
            interval_seconds=_int(form, "interval_seconds"), actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "last_status": row["last_status"]}, status_code=201)
    except common.ObservabilityError as exc:
        _err(exc)


@router.post("/health-checks/{check_id}/snapshots")
async def record_health_snapshot(check_id: int, request: Request,
                                 principal: Principal = Depends(require_capability("observability.execute"))):
    form = await _form(request)
    try:
        row = health.record_health_snapshot(principal, check_id, status=_one(form, "status"),
                                            latency_ms=_int(form, "latency_ms"),
                                            detail=_one(form, "detail") or None,
                                            actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.ObservabilityNotFound:
        raise HTTPException(404, "health check not found") from None
    except common.ObservabilityError as exc:
        _err(exc)


@router.get("/health-checks/{check_id}/snapshots")
def list_health_snapshots(check_id: int, request: Request,
                          principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse({"snapshots": common.as_json(health.list_health_snapshots(health_check_id=check_id))})


# --- diagnostics -------------------------------------------------------------

@router.get("/diagnostics")
def list_diagnostic_checks(request: Request, category: str | None = None,
                           principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse({"diagnostics": [
        {"id": d["id"], "code": d["code"], "name": d["name"], "category": d["category"]}
        for d in health.list_diagnostic_checks(category=category)]})


@router.post("/diagnostics")
async def create_diagnostic_check(request: Request,
                                  principal: Principal = Depends(require_capability("observability.manage"))):
    form = await _form(request)
    try:
        row = health.create_diagnostic_check(
            principal, code=_one(form, "code"), name=_one(form, "name"),
            category=_one(form, "category") or "other", actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"]}, status_code=201)
    except common.ObservabilityError as exc:
        _err(exc)


@router.post("/diagnostics/{check_id}/results")
async def record_diagnostic_result(check_id: int, request: Request,
                                   principal: Principal = Depends(require_capability("observability.execute"))):
    form = await _form(request)
    try:
        row = health.record_diagnostic_result(principal, check_id, status=_one(form, "status"),
                                              summary=_one(form, "summary") or None,
                                              detail=_one(form, "detail") or None,
                                              actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.ObservabilityNotFound:
        raise HTTPException(404, "diagnostic check not found") from None
    except common.ObservabilityError as exc:
        _err(exc)


@router.get("/diagnostics/{check_id}/results")
def list_diagnostic_results(check_id: int, request: Request,
                            principal: Principal = Depends(require_capability("observability.view"))):
    # Sensitive detail only for observability.audit holders (kept server-side otherwise).
    include_detail = principal.can("observability.audit")
    return JSONResponse({"results": common.as_json(
        health.list_diagnostic_results(diagnostic_check_id=check_id, include_detail=include_detail))})


# --- telemetry ---------------------------------------------------------------

@router.get("/telemetry/sources")
def list_telemetry_sources(request: Request,
                           principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse({"sources": telemetry.list_sources()})


@router.post("/telemetry/sources")
async def create_telemetry_source(request: Request,
                                  principal: Principal = Depends(require_capability("observability.manage"))):
    form = await _form(request)
    try:
        row = telemetry.create_source(principal, code=_one(form, "code"), name=_one(form, "name"),
                                      source_type=_one(form, "source_type") or "custom",
                                      reference=_one(form, "reference") or None,
                                      actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"]}, status_code=201)
    except common.ObservabilityError as exc:
        _err(exc)


@router.get("/telemetry/metrics")
def list_telemetry_metrics(request: Request, telemetry_source_id: int | None = None,
                           principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse({"metrics": [
        {"id": m["id"], "code": m["code"], "name": m["name"], "metric_kind": m["metric_kind"],
         "last_value": m["last_value"]} for m in telemetry.list_metrics(telemetry_source_id=telemetry_source_id)]})


@router.post("/telemetry/metrics")
async def create_telemetry_metric(request: Request,
                                  principal: Principal = Depends(require_capability("observability.manage"))):
    form = await _form(request)
    try:
        row = telemetry.create_metric(
            principal, code=_one(form, "code"), name=_one(form, "name"),
            telemetry_source_id=_int(form, "telemetry_source_id"),
            metric_kind=_one(form, "metric_kind") or "gauge", unit=_one(form, "unit") or None,
            analytics_metric_key=_one(form, "analytics_metric_key") or None,
            actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"]}, status_code=201)
    except common.ObservabilityError as exc:
        _err(exc)


@router.post("/telemetry/metrics/{metric_id}/collect")
async def collect_telemetry_metric(metric_id: int, request: Request,
                                   principal: Principal = Depends(require_capability("observability.execute"))):
    form = await _form(request)
    try:
        row = telemetry.collect_metric(principal, metric_id, float(_one(form, "value") or 0),
                                       actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "last_value": row["last_value"], "breach": row["breach"]})
    except common.ObservabilityNotFound:
        raise HTTPException(404, "telemetry metric not found") from None
    except common.ObservabilityError as exc:
        _err(exc)


# --- alert rules / alerts / suppressions -------------------------------------

@router.get("/alert-rules")
def list_alert_rules(request: Request,
                     principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse({"alert_rules": [
        {"id": r["id"], "code": r["code"], "name": r["name"], "severity": r["severity"],
         "enabled": r["enabled"]} for r in alerts.list_rules()]})


@router.post("/alert-rules")
async def create_alert_rule(request: Request,
                            principal: Principal = Depends(require_capability("observability.manage"))):
    form = await _form(request)
    try:
        row = alerts.create_rule(
            principal, code=_one(form, "code"), name=_one(form, "name"),
            telemetry_metric_id=_int(form, "telemetry_metric_id"), service_id=_int(form, "service_id"),
            severity=_one(form, "severity") or "warning", actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"]}, status_code=201)
    except common.ObservabilityError as exc:
        _err(exc)


@router.get("/alerts")
def list_alerts(request: Request, status: str | None = None, severity: str | None = None,
                principal: Principal = Depends(require_capability("observability.view"))):
    result = alerts.list_alerts(status=status, severity=severity)
    return JSONResponse({"alerts": [
        {"id": a["id"], "code": a["code"], "title": a["title"], "severity": a["severity"],
         "status": a["status"]} for a in result["rows"]], "total": result["total"]})


@router.post("/alerts")
async def raise_alert(request: Request,
                      principal: Principal = Depends(require_capability("observability.execute"))):
    form = await _form(request)
    try:
        row = alerts.raise_alert(
            principal, code=_one(form, "code"), title=_one(form, "title"),
            alert_rule_id=_int(form, "alert_rule_id"), service_id=_int(form, "service_id"),
            severity=_one(form, "severity") or "warning",
            notification_ref=_one(form, "notification_ref") or None, actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.ObservabilityError as exc:
        _err(exc)


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int, request: Request,
                      principal: Principal = Depends(require_capability("observability.execute"))):
    try:
        row = alerts.acknowledge_alert(principal, alert_id, actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]})
    except common.ObservabilityNotFound:
        raise HTTPException(404, "alert not found") from None


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, request: Request,
                  principal: Principal = Depends(require_capability("observability.execute"))):
    try:
        row = alerts.resolve_alert(principal, alert_id, actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]})
    except common.ObservabilityNotFound:
        raise HTTPException(404, "alert not found") from None


@router.get("/suppressions")
def list_suppressions(request: Request, active_only: bool = False,
                      principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse({"suppressions": common.as_json(alerts.list_suppressions(active_only=active_only))})


@router.post("/suppressions")
async def create_suppression(request: Request,
                             principal: Principal = Depends(require_capability("observability.manage"))):
    form = await _form(request)
    try:
        row = alerts.create_suppression(
            principal, code=_one(form, "code"), name=_one(form, "name"),
            alert_rule_id=_int(form, "alert_rule_id"), service_id=_int(form, "service_id"),
            reason=_one(form, "reason") or None, actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"]}, status_code=201)
    except common.ObservabilityError as exc:
        _err(exc)


# --- maintenance windows -----------------------------------------------------

@router.get("/maintenance-windows")
def list_maintenance_windows(request: Request, status: str | None = None,
                             principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse({"maintenance_windows": common.as_json(alerts.list_maintenance_windows(status=status))})


@router.post("/maintenance-windows")
async def create_maintenance_window(request: Request,
                                    principal: Principal = Depends(require_capability("observability.manage"))):
    form = await _form(request)
    try:
        row = alerts.create_maintenance_window(
            principal, code=_one(form, "code"), title=_one(form, "title"),
            service_id=_int(form, "service_id"), actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.ObservabilityError as exc:
        _err(exc)


@router.post("/maintenance-windows/{window_id}/status")
async def set_maintenance_status(window_id: int, request: Request,
                                 principal: Principal = Depends(require_capability("observability.execute"))):
    form = await _form(request)
    try:
        row = alerts.set_maintenance_status(principal, window_id, _one(form, "status"),
                                            actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]})
    except common.ObservabilityNotFound:
        raise HTTPException(404, "maintenance window not found") from None
    except common.ObservabilityError as exc:
        _err(exc)


# --- runtime snapshots -------------------------------------------------------

@router.get("/runtime-snapshots")
def list_runtime_snapshots(request: Request,
                           principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse({"snapshots": common.as_json(health.list_runtime_snapshots())})


@router.post("/runtime-snapshots")
def capture_runtime_snapshot(request: Request,
                             principal: Principal = Depends(require_capability("observability.execute"))):
    row = health.capture_runtime_snapshot(principal, actor_user_id=principal.user_id)
    return JSONResponse({"id": row["id"], "summary": row["summary"],
                         "migration_in_sync": row["migration_in_sync"]}, status_code=201)


# --- reliability incidents / findings ----------------------------------------

@router.get("/incidents")
def list_incidents(request: Request, status: str | None = None, severity: str | None = None,
                   principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse({"incidents": [
        {"id": i["id"], "code": i["code"], "title": i["title"], "severity": i["severity"],
         "status": i["status"]} for i in incidents.list_incidents(principal, status=status, severity=severity)]})


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: int, request: Request,
                 principal: Principal = Depends(require_capability("observability.view"))):
    try:
        inc = incidents.get_incident(principal, incident_id)
    except common.ObservabilityNotFound:
        raise HTTPException(404, "incident not found") from None
    if inc is None:
        raise HTTPException(404, "incident not found")
    return JSONResponse(common.as_json(inc))


@router.post("/incidents")
async def open_incident(request: Request,
                        principal: Principal = Depends(require_capability("observability.manage"))):
    form = await _form(request)
    try:
        row = incidents.open_incident(
            principal, code=_one(form, "code"), title=_one(form, "title"),
            severity=_one(form, "severity") or "medium", service_id=_int(form, "service_id"),
            person_id=_int(form, "person_id"), household_id=_int(form, "household_id"),
            actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.ObservabilityError as exc:
        _err(exc)


@router.post("/incidents/{incident_id}/status")
async def set_incident_status(incident_id: int, request: Request,
                              principal: Principal = Depends(require_capability("observability.execute"))):
    form = await _form(request)
    try:
        row = incidents.set_incident_status(principal, incident_id, _one(form, "status"),
                                            actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]})
    except common.ObservabilityNotFound:
        raise HTTPException(404, "incident not found") from None
    except common.ObservabilityError as exc:
        _err(exc)


@router.get("/findings")
def list_findings(request: Request, status: str | None = None, source: str | None = None,
                  principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse({"findings": [
        {"id": f["id"], "title": f["title"], "severity": f["severity"], "status": f["status"],
         "source": f["source"]} for f in incidents.list_findings(status=status, source=source)]})


@router.post("/findings")
async def create_finding(request: Request,
                         principal: Principal = Depends(require_capability("observability.manage"))):
    form = await _form(request)
    try:
        row = incidents.create_finding(
            principal, title=_one(form, "title"), severity=_one(form, "severity") or "medium",
            source=_one(form, "source") or "manual", incident_id=_int(form, "incident_id"),
            service_id=_int(form, "service_id"), security_finding_id=_int(form, "security_finding_id"),
            integration_connector_id=_int(form, "integration_connector_id"),
            actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.ObservabilityError as exc:
        _err(exc)


@router.post("/findings/{finding_id}/status")
async def set_finding_status(finding_id: int, request: Request,
                             principal: Principal = Depends(require_capability("observability.execute"))):
    form = await _form(request)
    try:
        row = incidents.set_finding_status(principal, finding_id, _one(form, "status"),
                                           actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]})
    except common.ObservabilityNotFound:
        raise HTTPException(404, "finding not found") from None
    except common.ObservabilityError as exc:
        _err(exc)


# --- scans (automation-style, manual trigger) --------------------------------

@router.post("/scans/run")
def run_scans(request: Request,
              principal: Principal = Depends(require_capability("observability.execute"))):
    return JSONResponse(scans.run_due_scans(principal, actor_user_id=principal.user_id))


# --- audit history -----------------------------------------------------------

@router.get("/audit/{entity_type}/{entity_id}")
def audit_history(entity_type: str, entity_id: int, request: Request,
                  principal: Principal = Depends(require_capability("observability.audit"))):
    return JSONResponse({"events": common.as_json(
        svc.audit_history(principal, entity_type=entity_type, entity_id=entity_id))})
