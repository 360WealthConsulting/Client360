"""Enterprise Security routes (Phase D.25) — policies, providers, secrets, certificates, incidents.

New ``/security`` prefix. It matches no middleware RULE, so each endpoint enforces its ``security.*``
capability in-route. Reads/overview require ``security.view``; creating/configuring metadata requires
``security.manage``; approvals / secret rotation / certificate renewal / incident & exception
decisions / running reviews require ``security.execute``; audit history requires ``security.audit``.
Secret ciphertext is never returned (it is stripped from every response) and no cryptographic key or
plaintext secret ever reaches a template — sensitive security metadata stays server-side (ADR-005).
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.security import (
    common,
    incidents,
    policies,
    providers,
    scans,
    secrets,
)
from app.services.security import service as svc
from app.templating import install_filters

router = APIRouter(prefix="/security", tags=["security"])
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
def overview(request: Request, principal: Principal = Depends(require_capability("security.view"))):
    return templates.TemplateResponse(request=request, name="security/overview.html", context={
        "principal": principal, "metrics": svc.overview_metrics(principal),
        "policies": policies.list_policies(), "incidents": incidents.list_incidents(principal),
        "can_manage": principal.can("security.manage"),
        "can_execute": principal.can("security.execute")})


@router.get("/overview")
def overview_json(request: Request, principal: Principal = Depends(require_capability("security.view"))):
    return JSONResponse({"metrics": svc.overview_metrics(principal)})


# --- policies ----------------------------------------------------------------

@router.get("/policies")
def list_policies(request: Request, policy_type: str | None = None, status: str | None = None,
                  principal: Principal = Depends(require_capability("security.view"))):
    return JSONResponse({"policies": [
        {"id": p["id"], "code": p["code"], "name": p["name"], "policy_type": p["policy_type"],
         "status": p["status"], "version": p["version"]}
        for p in policies.list_policies(policy_type=policy_type, status=status)]})


@router.get("/policies/{policy_id}")
def get_policy(policy_id: int, request: Request,
               principal: Principal = Depends(require_capability("security.view"))):
    pol = policies.get_policy(principal, policy_id)
    if pol is None:
        raise HTTPException(404, "policy not found")
    return JSONResponse(common.as_json(pol))


@router.post("/policies")
async def create_policy(request: Request,
                        principal: Principal = Depends(require_capability("security.manage"))):
    form = await _form(request)
    try:
        row = policies.create_policy(
            principal, code=_one(form, "code"), name=_one(form, "name"),
            policy_type=_one(form, "policy_type") or "security",
            description=_one(form, "description") or None, actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.SecurityError as exc:
        _err(exc)


@router.post("/policies/{policy_id}/status")
async def set_policy_status(policy_id: int, request: Request,
                            principal: Principal = Depends(require_capability("security.execute"))):
    form = await _form(request)
    try:
        row = policies.set_policy_status(principal, policy_id, _one(form, "status"),
                                         actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]})
    except common.SecurityNotFound:
        raise HTTPException(404, "policy not found") from None
    except common.SecurityError as exc:
        _err(exc)


# --- configurations (hardening baseline) -------------------------------------

@router.get("/configurations")
def list_configurations(request: Request, category: str | None = None,
                        principal: Principal = Depends(require_capability("security.view"))):
    return JSONResponse({"configurations": policies.list_configurations(category=category)})


@router.post("/configurations")
async def upsert_configuration(request: Request,
                               principal: Principal = Depends(require_capability("security.manage"))):
    form = await _form(request)
    try:
        row = policies.upsert_configuration(
            principal, config_key=_one(form, "config_key"), name=_one(form, "name"),
            category=_one(form, "category") or "hardening",
            applied=(_one(form, "applied") == "true"), actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "config_key": row["config_key"]}, status_code=201)
    except common.SecurityError as exc:
        _err(exc)


# --- identity / authentication / federation providers ------------------------

@router.get("/providers")
def list_providers(request: Request, provider_kind: str | None = None,
                   principal: Principal = Depends(require_capability("security.view"))):
    return JSONResponse({"providers": [
        {"id": p["id"], "code": p["code"], "name": p["name"], "provider_kind": p["provider_kind"],
         "protocol": p["protocol"], "enabled": p["enabled"], "status": p["status"]}
        for p in providers.list_providers(provider_kind=provider_kind)]})


@router.post("/providers")
async def create_provider(request: Request,
                          principal: Principal = Depends(require_capability("security.manage"))):
    form = await _form(request)
    try:
        row = providers.create_provider(
            principal, code=_one(form, "code"), name=_one(form, "name"),
            provider_kind=_one(form, "provider_kind") or "authentication",
            protocol=_one(form, "protocol") or "oauth2", actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "enabled": row["enabled"]}, status_code=201)
    except common.SecurityError as exc:
        _err(exc)


@router.post("/providers/{provider_id}/status")
async def set_provider_status(provider_id: int, request: Request,
                              principal: Principal = Depends(require_capability("security.execute"))):
    form = await _form(request)
    try:
        row = providers.set_provider_status(principal, provider_id, _one(form, "status"),
                                            actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "enabled": row["enabled"], "status": row["status"]})
    except common.SecurityNotFound:
        raise HTTPException(404, "provider not found") from None
    except common.SecurityError as exc:
        _err(exc)


# --- secret references (never expose ciphertext) -----------------------------

@router.get("/secrets")
def list_secrets(request: Request, status: str | None = None,
                 principal: Principal = Depends(require_capability("security.view"))):
    # Ciphertext is stripped in the service; expose only reference metadata.
    return JSONResponse({"secrets": [
        {"id": s["id"], "code": s["code"], "name": s["name"], "reference_kind": s["reference_kind"],
         "status": s["status"], "rotation_schedule": s["rotation_schedule"],
         "next_rotation_at": s["next_rotation_at"].isoformat() if s.get("next_rotation_at") else None}
        for s in secrets.list_secret_references(status=status)]})


@router.post("/secrets")
async def create_secret(request: Request,
                        principal: Principal = Depends(require_capability("security.manage"))):
    form = await _form(request)
    try:
        row = secrets.create_secret_reference(
            principal, code=_one(form, "code"), name=_one(form, "name"),
            reference_kind=_one(form, "reference_kind") or "encrypted_secret",
            reference_id=_int(form, "reference_id"), secret=(_one(form, "secret") or None),
            rotation_schedule=_one(form, "rotation_schedule") or "manual",
            actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.SecurityError as exc:
        _err(exc)


@router.post("/secrets/{secret_id}/rotate")
async def rotate_secret(secret_id: int, request: Request,
                        principal: Principal = Depends(require_capability("security.execute"))):
    form = await _form(request)
    try:
        row = secrets.rotate_secret_reference(principal, secret_id, secret=(_one(form, "secret") or None),
                                              actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "last_rotated_at":
                             row["last_rotated_at"].isoformat() if row.get("last_rotated_at") else None})
    except common.SecurityNotFound:
        raise HTTPException(404, "secret reference not found") from None
    except common.SecurityError as exc:
        _err(exc)


# --- certificate references --------------------------------------------------

@router.get("/certificates")
def list_certificates(request: Request, status: str | None = None,
                      principal: Principal = Depends(require_capability("security.view"))):
    return JSONResponse({"certificates": [
        {"id": c["id"], "code": c["code"], "name": c["name"], "status": c["status"],
         "not_after": c["not_after"].isoformat() if c.get("not_after") else None}
        for c in secrets.list_certificates(status=status)]})


@router.post("/certificates")
async def create_certificate(request: Request,
                             principal: Principal = Depends(require_capability("security.manage"))):
    form = await _form(request)
    try:
        row = secrets.create_certificate_reference(
            principal, code=_one(form, "code"), name=_one(form, "name"),
            subject=_one(form, "subject") or None, issuer=_one(form, "issuer") or None,
            fingerprint=_one(form, "fingerprint") or None, actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.SecurityError as exc:
        _err(exc)


@router.post("/certificates/{cert_id}/renew")
async def renew_certificate(cert_id: int, request: Request,
                            principal: Principal = Depends(require_capability("security.execute"))):
    try:
        row = secrets.renew_certificate_reference(principal, cert_id, actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]})
    except common.SecurityNotFound:
        raise HTTPException(404, "certificate not found") from None
    except common.SecurityError as exc:
        _err(exc)


# --- incidents ---------------------------------------------------------------

@router.get("/incidents")
def list_incidents(request: Request, status: str | None = None, severity: str | None = None,
                   principal: Principal = Depends(require_capability("security.view"))):
    return JSONResponse({"incidents": [
        {"id": i["id"], "code": i["code"], "title": i["title"], "severity": i["severity"],
         "status": i["status"]} for i in incidents.list_incidents(principal, status=status, severity=severity)]})


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: int, request: Request,
                 principal: Principal = Depends(require_capability("security.view"))):
    try:
        inc = incidents.get_incident(principal, incident_id)
    except common.SecurityNotFound:
        raise HTTPException(404, "incident not found") from None
    if inc is None:
        raise HTTPException(404, "incident not found")
    return JSONResponse(common.as_json(inc))


@router.post("/incidents")
async def open_incident(request: Request,
                        principal: Principal = Depends(require_capability("security.manage"))):
    form = await _form(request)
    try:
        row = incidents.open_incident(
            principal, code=_one(form, "code"), title=_one(form, "title"),
            severity=_one(form, "severity") or "medium", category=_one(form, "category") or None,
            person_id=_int(form, "person_id"), household_id=_int(form, "household_id"),
            actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.SecurityError as exc:
        _err(exc)


@router.post("/incidents/{incident_id}/status")
async def set_incident_status(incident_id: int, request: Request,
                              principal: Principal = Depends(require_capability("security.execute"))):
    form = await _form(request)
    try:
        row = incidents.set_incident_status(principal, incident_id, _one(form, "status"),
                                            actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]})
    except common.SecurityNotFound:
        raise HTTPException(404, "incident not found") from None
    except common.SecurityError as exc:
        _err(exc)


# --- findings ----------------------------------------------------------------

@router.get("/findings")
def list_findings(request: Request, status: str | None = None, source: str | None = None,
                  principal: Principal = Depends(require_capability("security.view"))):
    return JSONResponse({"findings": [
        {"id": f["id"], "title": f["title"], "severity": f["severity"], "status": f["status"],
         "source": f["source"]} for f in incidents.list_findings(status=status, source=source)]})


@router.post("/findings")
async def create_finding(request: Request,
                         principal: Principal = Depends(require_capability("security.manage"))):
    form = await _form(request)
    try:
        row = incidents.create_finding(
            principal, title=_one(form, "title"), finding_type=_one(form, "finding_type") or "manual",
            severity=_one(form, "severity") or "medium", source=_one(form, "source") or "manual",
            governance_finding_id=_int(form, "governance_finding_id"),
            incident_id=_int(form, "incident_id"), actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.SecurityError as exc:
        _err(exc)


@router.post("/findings/{finding_id}/status")
async def set_finding_status(finding_id: int, request: Request,
                             principal: Principal = Depends(require_capability("security.execute"))):
    form = await _form(request)
    try:
        row = incidents.set_finding_status(principal, finding_id, _one(form, "status"),
                                           actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]})
    except common.SecurityNotFound:
        raise HTTPException(404, "finding not found") from None
    except common.SecurityError as exc:
        _err(exc)


# --- exceptions --------------------------------------------------------------

@router.get("/exceptions")
def list_exceptions(request: Request, status: str | None = None,
                    principal: Principal = Depends(require_capability("security.view"))):
    return JSONResponse({"exceptions": [
        {"id": e["id"], "code": e["code"], "title": e["title"], "status": e["status"]}
        for e in incidents.list_exceptions(status=status)]})


@router.post("/exceptions")
async def request_exception(request: Request,
                            principal: Principal = Depends(require_capability("security.manage"))):
    form = await _form(request)
    try:
        row = incidents.request_exception(
            principal, code=_one(form, "code"), title=_one(form, "title"),
            policy_id=_int(form, "policy_id"), justification=_one(form, "justification") or None,
            actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]}, status_code=201)
    except common.SecurityError as exc:
        _err(exc)


@router.post("/exceptions/{exception_id}/decision")
async def decide_exception(exception_id: int, request: Request,
                           principal: Principal = Depends(require_capability("security.execute"))):
    form = await _form(request)
    try:
        row = incidents.decide_exception(principal, exception_id, _one(form, "status"),
                                         actor_user_id=principal.user_id)
        return JSONResponse({"id": row["id"], "status": row["status"]})
    except common.SecurityNotFound:
        raise HTTPException(404, "exception not found") from None
    except common.SecurityError as exc:
        _err(exc)


# --- reviews (automation-style, manual trigger) ------------------------------

@router.post("/reviews/run")
def run_reviews(request: Request,
                principal: Principal = Depends(require_capability("security.execute"))):
    return JSONResponse(scans.run_due_reviews(principal, actor_user_id=principal.user_id))


# --- audit history -----------------------------------------------------------

@router.get("/audit/{entity_type}/{entity_id}")
def audit_history(entity_type: str, entity_id: int, request: Request,
                  principal: Principal = Depends(require_capability("security.audit"))):
    return JSONResponse({"events": common.as_json(
        svc.audit_history(principal, entity_type=entity_type, entity_id=entity_id))})
