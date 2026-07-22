"""Data Governance routes (Phase D.23) — lineage, quality, MDM, retention, records control.

New ``/governance`` prefix. It matches no middleware RULE, so each endpoint enforces its
``governance.*`` capability in-route; the service enforces record scope on client-anchored items.
Merge application, legal holds, and deletion approval require ``governance.review`` (or
``governance.admin``) — enforced in-route and re-checked in-service. Governance never mutates a
canonical record, never performs an unsafe merge, and never issues a hard DELETE.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.governance import catalog, common, mdm, quality, retention
from app.services.governance import service as svc
from app.templating import install_filters

router = APIRouter(prefix="/governance", tags=["governance"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


async def _form(request):
    return parse_qs((await request.body()).decode("utf-8"))


def _one(form, key):
    return (form.get(key, [""])[0]).strip()


def _int(form, key):
    v = _one(form, key)
    return int(v) if v else None


def _ids(form, key):
    return [int(v) for v in form.get(key, []) if v.strip().isdigit()] or \
           [int(x) for x in _one(form, key).split(",") if x.strip().isdigit()]


# --- overview + reads --------------------------------------------------------

@router.get("", response_class=HTMLResponse)
def overview(request: Request, status: str | None = None,
             principal: Principal = Depends(require_capability("governance.view"))):
    return templates.TemplateResponse(request=request, name="governance/overview.html", context={
        "principal": principal, "metrics": svc.overview_metrics(principal),
        "findings": quality.list_findings(principal, status=status or "open")["rows"][:50],
        "domains": catalog.list_domains(active_only=True),
        "can_manage": principal.can("governance.manage"),
        "can_review": principal.can("governance.review")})


@router.get("/domains")
def list_domains(request: Request, principal: Principal = Depends(require_capability("governance.view"))):
    return JSONResponse({"domains": [{"id": d["id"], "code": d["code"], "name": d["name"]}
                                     for d in catalog.list_domains()]})


@router.get("/elements")
def list_elements(request: Request, data_domain_id: int | None = None,
                  principal: Principal = Depends(require_capability("governance.view"))):
    return JSONResponse({"elements": [
        {"id": e["id"], "code": e["code"], "name": e["name"], "classification": e["classification"]}
        for e in catalog.list_elements(data_domain_id=data_domain_id)]})


@router.get("/rules")
def list_rules(request: Request, rule_type: str | None = None,
               principal: Principal = Depends(require_capability("governance.view"))):
    return JSONResponse({"rules": [
        {"id": r["id"], "code": r["code"], "name": r["name"], "rule_type": r["rule_type"],
         "severity": r["severity"], "active": r["active"]} for r in catalog.list_rules(rule_type=rule_type)]})


@router.get("/survivorship-rules")
def list_survivorship(request: Request, principal: Principal = Depends(require_capability("governance.view"))):
    return JSONResponse({"survivorship_rules": [
        {"id": r["id"], "code": r["code"], "name": r["name"], "strategy": r["strategy"]}
        for r in catalog.list_survivorship_rules()]})


@router.get("/findings")
def list_findings(request: Request, status: str | None = None, severity: str | None = None, page: int = 1,
                  principal: Principal = Depends(require_capability("governance.view"))):
    result = quality.list_findings(principal, status=status, severity=severity, page=page)
    return JSONResponse({"total": result["total"], "page": result["page"], "findings": [
        {"id": f["id"], "finding_type": f["finding_type"], "severity": f["severity"],
         "status": f["status"], "entity_type": f["entity_type"], "entity_id": f["entity_id"]}
        for f in result["rows"]]})


@router.get("/findings/{finding_id}")
def get_finding(request: Request, finding_id: int,
                principal: Principal = Depends(require_capability("governance.view"))):
    f = quality.get_finding(principal, finding_id)
    if f is None:
        raise HTTPException(404, "Not found")
    return JSONResponse({"id": f["id"], "finding_type": f["finding_type"], "status": f["status"],
                         "severity": f["severity"], "detail": f["detail"]})


@router.get("/findings/{finding_id}/audit")
def finding_audit(request: Request, finding_id: int,
                  principal: Principal = Depends(require_capability("governance.audit"))):
    if quality.get_finding(principal, finding_id) is None:
        raise HTTPException(404, "Not found")
    return JSONResponse({"history": [
        {"event_type": e["event_type"], "to_status": e["to_status"],
         "occurred_at": e["occurred_at"].isoformat()}
        for e in common.audit_history(principal, entity_type="finding", entity_id=finding_id)]})


@router.get("/candidates")
def list_candidates(request: Request, status: str | None = None, page: int = 1,
                    principal: Principal = Depends(require_capability("governance.view"))):
    result = mdm.list_candidates(principal, status=status, page=page)
    return JSONResponse({"total": result["total"], "candidates": [
        {"id": c["id"], "status": c["status"], "match_method": c["match_method"],
         "detected_by": c["detected_by"]} for c in result["rows"]]})


@router.get("/candidates/{candidate_id}")
def get_candidate(request: Request, candidate_id: int,
                  principal: Principal = Depends(require_capability("governance.view"))):
    c = mdm.get_candidate(principal, candidate_id)
    if c is None:
        raise HTTPException(404, "Not found")
    return JSONResponse({"id": c["id"], "status": c["status"], "source_contact_ids": c["source_contact_ids"]})


@router.get("/lineage/person/{person_id}")
def person_lineage(request: Request, person_id: int,
                   principal: Principal = Depends(require_capability("governance.view"))):
    from app.security.authorization import record_in_scope
    if not (principal.can("record.read_all") or record_in_scope(principal, "person", person_id)):
        raise HTTPException(404, "Not found")
    return JSONResponse({"lineage": [
        {"source_system": r["source_system"], "source_file": r["source_file"],
         "source_record_id": r["source_record_id"], "match_method": r["match_method"],
         "confirmed": r["confirmed"]} for r in mdm.person_lineage(principal, person_id)]})


@router.get("/lineage/{entity_type}/{entity_id}")
def entity_lineage(request: Request, entity_type: str, entity_id: int,
                   principal: Principal = Depends(require_capability("governance.view"))):
    return JSONResponse({"lineage": mdm.list_lineage(entity_type, entity_id)})


@router.get("/retention")
def list_retention(request: Request, status: str | None = None,
                   principal: Principal = Depends(require_capability("governance.view"))):
    return JSONResponse({"assignments": [
        {"id": a["id"], "entity_type": a["entity_type"], "entity_id": a["entity_id"],
         "status": a["status"], "expiration_date": a["expiration_date"].isoformat() if a["expiration_date"] else None}
        for a in retention.list_retention_assignments(status=status)]})


@router.get("/legal-holds")
def list_legal_holds(request: Request, status: str | None = None,
                     principal: Principal = Depends(require_capability("governance.view"))):
    return JSONResponse({"legal_holds": [
        {"id": h["id"], "code": h["code"], "name": h["name"], "entity_type": h["entity_type"],
         "entity_id": h["entity_id"], "status": h["status"]}
        for h in retention.list_legal_holds(status=status)]})


@router.get("/deletion-requests")
def list_deletions(request: Request, status: str | None = None,
                   principal: Principal = Depends(require_capability("governance.view"))):
    return JSONResponse({"deletion_requests": [
        {"id": d["id"], "request_type": d["request_type"], "entity_type": d["entity_type"],
         "status": d["status"], "legal_hold_blocked": d["legal_hold_blocked"]}
        for d in retention.list_deletion_requests(status=status)]})


@router.get("/cases")
def list_cases(request: Request, case_type: str | None = None, status: str | None = None,
               principal: Principal = Depends(require_capability("governance.view"))):
    return JSONResponse({"cases": [
        {"id": c["id"], "code": c["code"], "title": c["title"], "case_type": c["case_type"],
         "status": c["status"]} for c in retention.list_cases(case_type=case_type, status=status)]})


# --- catalog (manage) --------------------------------------------------------

@router.post("/domains")
async def create_domain(request: Request, principal: Principal = Depends(require_capability("governance.manage"))):
    form = await _form(request)
    try:
        catalog.create_domain(code=_one(form, "code"), name=_one(form, "name"),
                              description=_one(form, "description") or None, actor_user_id=principal.user_id)
    except common.GovernanceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/governance", status_code=303)


@router.post("/elements")
async def create_element(request: Request, principal: Principal = Depends(require_capability("governance.manage"))):
    form = await _form(request)
    try:
        catalog.create_element(data_domain_id=_int(form, "data_domain_id"), code=_one(form, "code"),
                               name=_one(form, "name"), entity_type=_one(form, "entity_type") or "person",
                               field_name=_one(form, "field_name") or None,
                               classification=_one(form, "classification") or "internal",
                               required=(_one(form, "required") == "1"), actor_user_id=principal.user_id)
    except common.GovernanceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/governance", status_code=303)


@router.post("/rules")
async def create_rule(request: Request, principal: Principal = Depends(require_capability("governance.manage"))):
    form = await _form(request)
    try:
        catalog.create_rule(code=_one(form, "code"), name=_one(form, "name"),
                            rule_type=_one(form, "rule_type"), entity_type=_one(form, "entity_type") or "person",
                            severity=_one(form, "severity") or "medium", actor_user_id=principal.user_id)
    except common.GovernanceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/governance", status_code=303)


@router.post("/rules/{rule_id}/run")
async def run_rule(request: Request, rule_id: int,
                   principal: Principal = Depends(require_capability("governance.manage"))):
    try:
        result = quality.run_check(principal, rule_id, run_type="manual", actor_user_id=principal.user_id)
    except common.GovernanceNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    return JSONResponse(result)


@router.post("/quality-scan")
async def quality_scan(request: Request, principal: Principal = Depends(require_capability("governance.manage"))):
    return JSONResponse(quality.run_all_active_checks(principal, run_type="manual",
                                                      actor_user_id=principal.user_id))


@router.post("/survivorship-rules")
async def create_survivorship(request: Request, principal: Principal = Depends(require_capability("governance.manage"))):
    form = await _form(request)
    try:
        catalog.create_survivorship_rule(code=_one(form, "code"), name=_one(form, "name"),
                                         strategy=_one(form, "strategy") or "most_recent",
                                         actor_user_id=principal.user_id)
    except common.GovernanceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/governance", status_code=303)


# --- findings ----------------------------------------------------------------

@router.post("/findings")
async def create_finding(request: Request, principal: Principal = Depends(require_capability("governance.manage"))):
    form = await _form(request)
    try:
        f = quality.create_finding(principal, entity_type=_one(form, "entity_type"),
                                   entity_id=_int(form, "entity_id"),
                                   finding_type=_one(form, "finding_type"),
                                   severity=_one(form, "severity") or "medium",
                                   person_id=_int(form, "person_id"), actor_user_id=principal.user_id)
    except common.GovernanceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"id": f["id"]}, status_code=201)


@router.post("/findings/{finding_id}/status")
async def finding_status(request: Request, finding_id: int,
                         principal: Principal = Depends(require_capability("governance.manage"))):
    form = await _form(request)
    try:
        quality.set_finding_status(principal, finding_id, _one(form, "status"), actor_user_id=principal.user_id)
    except common.GovernanceNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.GovernanceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/governance", status_code=303)


# --- duplicates + merges -----------------------------------------------------

@router.post("/candidates")
async def create_candidate(request: Request, principal: Principal = Depends(require_capability("governance.manage"))):
    form = await _form(request)
    try:
        c = mdm.create_candidate(principal, source_contact_ids=_ids(form, "source_contact_ids"),
                                 detected_by="manual", actor_user_id=principal.user_id)
    except common.GovernanceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"id": c["id"]}, status_code=201)


@router.post("/candidates/scan")
async def scan_candidates(request: Request, principal: Principal = Depends(require_capability("governance.manage"))):
    return JSONResponse(mdm.scan_duplicates(principal, actor_user_id=principal.user_id))


@router.post("/candidates/{candidate_id}/merge-decision")
async def merge_decision(request: Request, candidate_id: int,
                         principal: Principal = Depends(require_capability("governance.manage"))):
    form = await _form(request)
    try:
        d = mdm.record_merge_decision(principal, candidate_id, decision=_one(form, "decision"),
                                      notes=_one(form, "notes") or None,
                                      apply=(_one(form, "apply") == "1"), actor_user_id=principal.user_id)
    except common.GovernanceNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.GovernanceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"id": d["id"], "decision": d["decision"], "merged_person_id": d["merged_person_id"]})


@router.post("/lineage")
async def create_lineage(request: Request, principal: Principal = Depends(require_capability("governance.manage"))):
    form = await _form(request)
    try:
        row = mdm.record_lineage(principal, entity_type=_one(form, "entity_type"),
                                 entity_id=_int(form, "entity_id"), source_system=_one(form, "source_system"),
                                 source_reference=_one(form, "source_reference") or None,
                                 actor_user_id=principal.user_id)
    except common.GovernanceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"id": row["id"]}, status_code=201)


# --- retention + records control ---------------------------------------------

@router.post("/retention")
async def create_retention(request: Request, principal: Principal = Depends(require_capability("governance.manage"))):
    form = await _form(request)
    try:
        a = retention.create_retention_assignment(
            principal, entity_type=_one(form, "entity_type"), entity_id=_int(form, "entity_id"),
            retention_policy_id=_int(form, "retention_policy_id"),
            classification=_one(form, "classification") or None,
            person_id=_int(form, "person_id"), actor_user_id=principal.user_id)
    except common.GovernanceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"id": a["id"], "expiration_date": a["expiration_date"].isoformat() if a["expiration_date"] else None}, status_code=201)


@router.post("/retention/review")
async def retention_review(request: Request, principal: Principal = Depends(require_capability("governance.manage"))):
    return JSONResponse(retention.review_due_retention(principal, actor_user_id=principal.user_id))


@router.post("/legal-holds")
async def place_hold(request: Request, principal: Principal = Depends(require_capability("governance.review"))):
    form = await _form(request)
    try:
        h = retention.place_legal_hold(principal, code=_one(form, "code"), name=_one(form, "name"),
                                       entity_type=_one(form, "entity_type"), entity_id=_int(form, "entity_id"),
                                       reason=_one(form, "reason") or None, person_id=_int(form, "person_id"),
                                       actor_user_id=principal.user_id)
    except common.GovernanceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"id": h["id"], "status": h["status"]}, status_code=201)


@router.post("/legal-holds/{hold_id}/release")
async def release_hold(request: Request, hold_id: int,
                       principal: Principal = Depends(require_capability("governance.review"))):
    try:
        h = retention.release_legal_hold(principal, hold_id, actor_user_id=principal.user_id)
    except common.GovernanceNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    return JSONResponse({"id": h["id"], "status": h["status"]})


@router.post("/deletion-requests")
async def create_deletion(request: Request, principal: Principal = Depends(require_capability("governance.manage"))):
    form = await _form(request)
    try:
        d = retention.create_deletion_request(
            principal, entity_type=_one(form, "entity_type"), entity_id=_int(form, "entity_id"),
            request_type=_one(form, "request_type") or "deletion", reason=_one(form, "reason") or None,
            person_id=_int(form, "person_id"), actor_user_id=principal.user_id)
    except common.GovernanceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"id": d["id"], "status": d["status"], "legal_hold_blocked": d["legal_hold_blocked"]}, status_code=201)


@router.post("/deletion-requests/{request_id}/submit")
async def submit_deletion(request: Request, request_id: int,
                          principal: Principal = Depends(require_capability("governance.manage"))):
    try:
        d = retention.set_deletion_status(principal, request_id, "under_review", actor_user_id=principal.user_id)
    except common.GovernanceNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.GovernanceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"id": d["id"], "status": d["status"]})


@router.post("/deletion-requests/{request_id}/review")
async def review_deletion(request: Request, request_id: int,
                          principal: Principal = Depends(require_capability("governance.review"))):
    form = await _form(request)
    try:
        d = retention.review_deletion_request(principal, request_id, decision=_one(form, "decision"),
                                              evidence_reference=_one(form, "evidence_reference") or None,
                                              actor_user_id=principal.user_id)
    except common.GovernanceNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.GovernanceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"id": d["id"], "status": d["status"]})


@router.post("/deletion-requests/{request_id}/execute")
async def execute_deletion(request: Request, request_id: int,
                           principal: Principal = Depends(require_capability("governance.review"))):
    try:
        d = retention.execute_deletion(principal, request_id, actor_user_id=principal.user_id)
    except common.GovernanceNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.GovernanceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"id": d["id"], "status": d["status"]})


# --- cases -------------------------------------------------------------------

@router.post("/cases")
async def create_case(request: Request, principal: Principal = Depends(require_capability("governance.manage"))):
    form = await _form(request)
    try:
        case = retention.create_case(principal, code=_one(form, "code"), title=_one(form, "title"),
                                     case_type=_one(form, "case_type") or "remediation",
                                     finding_id=_int(form, "finding_id"),
                                     person_id=_int(form, "person_id"), actor_user_id=principal.user_id)
    except common.GovernanceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"id": case["id"]}, status_code=201)


@router.post("/cases/{case_id}/status")
async def case_status(request: Request, case_id: int,
                      principal: Principal = Depends(require_capability("governance.manage"))):
    form = await _form(request)
    try:
        retention.set_case_status(principal, case_id, _one(form, "status"), actor_user_id=principal.user_id)
    except common.GovernanceNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.GovernanceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/governance", status_code=303)
