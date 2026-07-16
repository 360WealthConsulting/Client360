"""Insurance Operations HTTP layer (Release 0.10.0, Phase 1).

Thin HTTP over app.services.insurance — no business logic here. Exposes the JSON
API under /api/v1/insurance and staff HTML consoles under /insurance. Capability
gating is per-endpoint; record scope is enforced in the service.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import insurance as ins
from app.services import insurance_commissions as com
from app.services import insurance_licensing as lic
from app.templating import templates

router = APIRouter(tags=["insurance"])

_NOT_FOUND = (ins.InsuranceNotFound, lic.LicensingNotFound, com.CommissionNotFound)
_BAD_INPUT = (ins.InsuranceError, lic.LicensingError, com.CommissionError)


def _run(fn):
    try:
        return fn()
    except _NOT_FOUND:
        raise HTTPException(404, "Not found") from None
    except PermissionError as exc:
        raise HTTPException(403 if "capability" in str(exc).lower() else 404, str(exc)) from None
    except _BAD_INPUT as exc:
        raise HTTPException(400, str(exc)) from None


# --- request models ----------------------------------------------------------

class CaseCreate(BaseModel):
    case_type: str
    household_id: int | None = None
    person_id: int | None = None
    objective: str | None = None


class PolicyCreate(BaseModel):
    carrier_id: int
    product_version_id: int
    case_id: int | None = None
    person_id: int | None = None
    household_id: int | None = None
    organization_id: int | None = None
    policy_number: str | None = None
    status: str = "proposed"
    face_amount: float | None = None
    premium_amount: float | None = None
    premium_mode: str | None = None


class StatusUpdate(BaseModel):
    status: str


class CoverageBody(BaseModel):
    coverage_type: str
    face_amount: float | None = None


class RiderBody(BaseModel):
    rider_type: str
    description: str | None = None
    face_amount: float | None = None


class PartyBody(BaseModel):
    party_role: str
    party_entity_type: str
    party_entity_id: int
    share_percentage: float | None = None
    designation: str | None = None
    is_primary_insured: bool = False


class ProducerBody(BaseModel):
    producer_entity_type: str
    producer_entity_id: int
    producer_role: str
    split_percentage: float | None = None


def _actor(request: Request, principal: Principal):
    return {"actor_user_id": principal.user_id,
            "request_id": getattr(request.state, "request_id", None)}


# --- JSON API ----------------------------------------------------------------

@router.post("/api/v1/insurance/cases", status_code=201)
def api_case_create(payload: CaseCreate, request: Request,
                    principal: Principal = Depends(require_capability("insurance.write"))):
    return _run(lambda: ins.create_case(principal, **payload.model_dump(), **_actor(request, principal)))


@router.post("/api/v1/insurance/policies", status_code=201)
def api_policy_create(payload: PolicyCreate, request: Request,
                      principal: Principal = Depends(require_capability("insurance.write"))):
    return _run(lambda: ins.create_policy(principal, **payload.model_dump(), **_actor(request, principal)))


@router.get("/api/v1/insurance/policies")
def api_policy_list(status: str = "", carrier_id: int | None = None,
                    principal: Principal = Depends(require_capability("insurance.read"))):
    return {"policies": _run(lambda: ins.list_policies(principal, status=status or None, carrier_id=carrier_id))}


@router.get("/api/v1/insurance/policies/{policy_id}")
def api_policy_get(policy_id: int, principal: Principal = Depends(require_capability("insurance.read"))):
    return _run(lambda: ins.get_policy(principal, policy_id))


@router.patch("/api/v1/insurance/policies/{policy_id}/status")
def api_policy_status(policy_id: int, payload: StatusUpdate, request: Request,
                      principal: Principal = Depends(require_capability("insurance.write"))):
    return _run(lambda: ins.update_policy_status(principal, policy_id, payload.status, **_actor(request, principal)))


@router.post("/api/v1/insurance/policies/{policy_id}/coverages", status_code=201)
def api_coverage_add(policy_id: int, payload: CoverageBody,
                     principal: Principal = Depends(require_capability("insurance.write"))):
    return _run(lambda: ins.add_coverage(principal, policy_id, **payload.model_dump()))


@router.post("/api/v1/insurance/policies/{policy_id}/riders", status_code=201)
def api_rider_add(policy_id: int, payload: RiderBody,
                  principal: Principal = Depends(require_capability("insurance.write"))):
    return _run(lambda: ins.add_rider(principal, policy_id, **payload.model_dump()))


@router.post("/api/v1/insurance/policies/{policy_id}/parties", status_code=201)
def api_party_add(policy_id: int, payload: PartyBody, request: Request,
                  principal: Principal = Depends(require_capability("insurance.write"))):
    return _run(lambda: ins.add_party(principal, policy_id, **payload.model_dump(), **_actor(request, principal)))


@router.post("/api/v1/insurance/policies/{policy_id}/producers", status_code=201)
def api_producer_add(policy_id: int, payload: ProducerBody,
                     principal: Principal = Depends(require_capability("insurance.write"))):
    return _run(lambda: ins.add_producer(principal, policy_id, **payload.model_dump()))


# --- HTML consoles -----------------------------------------------------------

@router.get("/insurance", response_class=HTMLResponse)
def console_book(request: Request, status: str = "",
                 principal: Principal = Depends(require_capability("insurance.read"))):
    policies = _run(lambda: ins.list_policies(principal, status=status or None))
    return templates.TemplateResponse(request=request, name="insurance/book.html",
                                      context={"policies": policies, "status": status, "principal": principal})


@router.get("/insurance/policies/{policy_id}", response_class=HTMLResponse)
def console_policy(policy_id: int, request: Request,
                   principal: Principal = Depends(require_capability("insurance.read"))):
    policy = _run(lambda: ins.get_policy(principal, policy_id))
    return templates.TemplateResponse(request=request, name="insurance/policy.html",
                                      context={"policy": policy, "principal": principal})


# --- Phase 2 (non-regulated): cases, requirements, underwriting status --------

class CaseStatusUpdate(BaseModel):
    status: str


class RequirementCreate(BaseModel):
    requirement_type: str
    case_id: int | None = None
    policy_id: int | None = None
    description: str | None = None
    due_date: str | None = None
    document_id: int | None = None


class RequirementSatisfy(BaseModel):
    document_id: int | None = None


class UnderwritingStatus(BaseModel):
    underwriting_status: str


@router.get("/api/v1/insurance/cases")
def api_case_list(status: str = "", principal: Principal = Depends(require_capability("insurance.read"))):
    return {"cases": _run(lambda: ins.list_cases(principal, status=status or None))}


@router.get("/api/v1/insurance/cases/{case_id}")
def api_case_get(case_id: int, principal: Principal = Depends(require_capability("insurance.read"))):
    return _run(lambda: ins.get_case(principal, case_id))


@router.patch("/api/v1/insurance/cases/{case_id}/status")
def api_case_status(case_id: int, payload: CaseStatusUpdate, request: Request,
                    principal: Principal = Depends(require_capability("insurance.write"))):
    return _run(lambda: ins.update_case_status(principal, case_id, payload.status, **_actor(request, principal)))


@router.patch("/api/v1/insurance/policies/{policy_id}/underwriting")
def api_underwriting_status(policy_id: int, payload: UnderwritingStatus, request: Request,
                            principal: Principal = Depends(require_capability("insurance.write"))):
    return _run(lambda: ins.set_underwriting_status(
        principal, policy_id, payload.underwriting_status, **_actor(request, principal)))


@router.post("/api/v1/insurance/requirements", status_code=201)
def api_requirement_create(payload: RequirementCreate, request: Request,
                           principal: Principal = Depends(require_capability("insurance.write"))):
    return _run(lambda: ins.request_requirement(principal, **payload.model_dump(), **_actor(request, principal)))


@router.patch("/api/v1/insurance/requirements/{requirement_id}/satisfy")
def api_requirement_satisfy(requirement_id: int, payload: RequirementSatisfy, request: Request,
                            principal: Principal = Depends(require_capability("insurance.write"))):
    return _run(lambda: ins.satisfy_requirement(
        principal, requirement_id, document_id=payload.document_id, **_actor(request, principal)))


@router.get("/api/v1/insurance/requirements")
def api_requirement_list(case_id: int | None = None, policy_id: int | None = None, open_only: bool = False,
                         principal: Principal = Depends(require_capability("insurance.read"))):
    return {"requirements": _run(lambda: ins.list_requirements(
        principal, case_id=case_id, policy_id=policy_id, open_only=open_only))}


@router.get("/insurance/cases/{case_id}", response_class=HTMLResponse)
def console_case(case_id: int, request: Request,
                 principal: Principal = Depends(require_capability("insurance.read"))):
    case = _run(lambda: ins.get_case(principal, case_id))
    return templates.TemplateResponse(request=request, name="insurance/case.html",
                                      context={"case": case, "principal": principal})


# --- operational reporting (non-regulated) -----------------------------------

@router.get("/api/v1/insurance/reporting")
def api_reporting(principal: Principal = Depends(require_capability("insurance.read"))):
    from app.services import insurance_reporting
    return _run(lambda: insurance_reporting.pipeline_report(principal))


@router.get("/insurance/reporting", response_class=HTMLResponse)
def console_reporting(request: Request,
                      principal: Principal = Depends(require_capability("insurance.read"))):
    from app.services import insurance_reporting
    report = _run(lambda: insurance_reporting.pipeline_report(principal))
    return templates.TemplateResponse(request=request, name="insurance/reporting.html",
                                      context={"report": report, "principal": principal})


# --- Phase 3 (non-regulated): in-force servicing reviews + obligation calendar ---

class ReviewCreate(BaseModel):
    review_type: str
    due_date: date
    policy_id: int | None = None
    case_id: int | None = None
    scheduled_date: date | None = None
    reviewer_user_id: int | None = None
    notes: str | None = None


class ReviewStatusUpdate(BaseModel):
    status: str
    scheduled_date: date | None = None


class ReviewComplete(BaseModel):
    completed_date: date | None = None
    next_review_date: date | None = None
    outcome_note: str | None = None


@router.get("/api/v1/insurance/reviews")
def api_review_list(status: str = "", policy_id: int | None = None, case_id: int | None = None,
                    principal: Principal = Depends(require_capability("insurance.read"))):
    return {"reviews": _run(lambda: ins.list_reviews(
        principal, status=status or None, policy_id=policy_id, case_id=case_id))}


@router.get("/api/v1/insurance/reviews/report")
def api_review_report(principal: Principal = Depends(require_capability("insurance.read"))):
    from app.services import insurance_reporting
    return _run(lambda: insurance_reporting.review_report(principal))


@router.post("/api/v1/insurance/reviews/scan")
def api_review_scan(principal: Principal = Depends(require_capability("insurance.write"))):
    """Operational obligation-calendar scan: flag past-due reviews overdue and raise the
    shared operational exception. Idempotent. No compliance determination."""
    from app.services import insurance_detectors
    return _run(lambda: insurance_detectors.run_insurance_review_scan(actor_user_id=principal.user_id))


@router.post("/api/v1/insurance/reviews", status_code=201)
def api_review_create(payload: ReviewCreate, request: Request,
                      principal: Principal = Depends(require_capability("insurance.write"))):
    return _run(lambda: ins.schedule_review(principal, **payload.model_dump(), **_actor(request, principal)))


@router.patch("/api/v1/insurance/reviews/{review_id}/status")
def api_review_status(review_id: int, payload: ReviewStatusUpdate, request: Request,
                      principal: Principal = Depends(require_capability("insurance.write"))):
    return _run(lambda: ins.update_review_status(
        principal, review_id, payload.status, scheduled_date=payload.scheduled_date,
        **_actor(request, principal)))


@router.patch("/api/v1/insurance/reviews/{review_id}/complete")
def api_review_complete(review_id: int, payload: ReviewComplete, request: Request,
                        principal: Principal = Depends(require_capability("insurance.write"))):
    return _run(lambda: ins.complete_review(
        principal, review_id, **payload.model_dump(), **_actor(request, principal)))


@router.get("/insurance/reviews", response_class=HTMLResponse)
def console_reviews(request: Request, status: str = "",
                    principal: Principal = Depends(require_capability("insurance.read"))):
    from app.services import insurance_reporting
    reviews = _run(lambda: ins.list_reviews(principal, status=status or None))
    metrics = _run(lambda: insurance_reporting.review_report(principal))
    return templates.TemplateResponse(request=request, name="insurance/reviews.html",
                                      context={"reviews": reviews, "metrics": metrics,
                                               "status": status, "principal": principal})


# --- Phase 4 (non-regulated): producer licensing & CE records + expiry reminders ---

class LicenseCreate(BaseModel):
    producer_user_id: int
    state: str
    license_number: str | None = None
    npn: str | None = None
    lines: list[str] | None = None
    status: str = "active"
    issue_date: date | None = None
    expiry_date: date | None = None
    notes: str | None = None


class LicenseUpdate(BaseModel):
    state: str | None = None
    license_number: str | None = None
    npn: str | None = None
    lines: list[str] | None = None
    status: str | None = None
    issue_date: date | None = None
    expiry_date: date | None = None
    notes: str | None = None


class CeCreate(BaseModel):
    producer_user_id: int
    state: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    credits_required: float | None = None
    credits_completed: float | None = None
    status: str = "in_progress"
    notes: str | None = None


class CeUpdate(BaseModel):
    state: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    credits_required: float | None = None
    credits_completed: float | None = None
    status: str | None = None
    notes: str | None = None


@router.get("/api/v1/insurance/licenses")
def api_license_list(producer_user_id: int | None = None, state: str = "", status: str = "",
                     principal: Principal = Depends(require_capability("insurance.licensing.read"))):
    return {"licenses": _run(lambda: lic.list_licenses(
        principal, producer_user_id=producer_user_id, state=state or None, status=status or None))}


@router.post("/api/v1/insurance/licenses", status_code=201)
def api_license_create(payload: LicenseCreate, request: Request,
                       principal: Principal = Depends(require_capability("insurance.licensing.write"))):
    return _run(lambda: lic.record_license(principal, **payload.model_dump(), **_actor(request, principal)))


@router.patch("/api/v1/insurance/licenses/{license_id}")
def api_license_update(license_id: int, payload: LicenseUpdate, request: Request,
                       principal: Principal = Depends(require_capability("insurance.licensing.write"))):
    return _run(lambda: lic.update_license(principal, license_id, **payload.model_dump(),
                                           **_actor(request, principal)))


@router.get("/api/v1/insurance/ce")
def api_ce_list(producer_user_id: int | None = None, status: str = "",
                principal: Principal = Depends(require_capability("insurance.licensing.read"))):
    return {"ce_records": _run(lambda: lic.list_ce(
        principal, producer_user_id=producer_user_id, status=status or None))}


@router.post("/api/v1/insurance/ce", status_code=201)
def api_ce_create(payload: CeCreate, request: Request,
                  principal: Principal = Depends(require_capability("insurance.licensing.write"))):
    return _run(lambda: lic.record_ce(principal, **payload.model_dump(), **_actor(request, principal)))


@router.patch("/api/v1/insurance/ce/{ce_id}")
def api_ce_update(ce_id: int, payload: CeUpdate, request: Request,
                  principal: Principal = Depends(require_capability("insurance.licensing.write"))):
    return _run(lambda: lic.update_ce(principal, ce_id, **payload.model_dump(),
                                      **_actor(request, principal)))


@router.get("/api/v1/insurance/licensing/report")
def api_licensing_report(principal: Principal = Depends(require_capability("insurance.licensing.read"))):
    from app.services import insurance_reporting
    return _run(lambda: insurance_reporting.licensing_report(principal))


@router.post("/api/v1/insurance/licensing/scan")
def api_licensing_scan(principal: Principal = Depends(require_capability("insurance.licensing.write"))):
    """Operational expiry-reminder scan for producer licenses and CE periods. Idempotent.
    No licensing-validation or CE-satisfaction determination."""
    from app.services import insurance_detectors
    return _run(lambda: insurance_detectors.run_insurance_licensing_scan(actor_user_id=principal.user_id))


@router.get("/insurance/licensing", response_class=HTMLResponse)
def console_licensing(request: Request,
                      principal: Principal = Depends(require_capability("insurance.licensing.read"))):
    from app.services import insurance_reporting
    licenses = _run(lambda: lic.list_licenses(principal))
    ce_records = _run(lambda: lic.list_ce(principal))
    report = _run(lambda: insurance_reporting.licensing_report(principal))
    return templates.TemplateResponse(request=request, name="insurance/licensing.html",
                                      context={"licenses": licenses, "ce_records": ce_records,
                                               "report": report, "principal": principal})


# --- Phase 5 (non-regulated): commissions — ledger, statements, reconciliation ---

class CommissionCreate(BaseModel):
    policy_id: int
    producer_entity_type: str
    producer_entity_id: int
    expected_amount: float
    schedule: str = "first_year"
    producer_role: str = "writing_agent"
    split_percentage: float | None = None
    period_label: str | None = None
    due_date: date | None = None
    notes: str | None = None


class CommissionGenerate(BaseModel):
    basis_amount: float
    schedule: str = "first_year"
    period_label: str | None = None
    due_date: date | None = None


class CommissionReceived(BaseModel):
    received_amount: float
    statement_id: int | None = None


class StatementLineBody(BaseModel):
    amount: float
    policy_number: str | None = None
    policy_id: int | None = None
    producer_reference: str | None = None
    schedule: str | None = None
    notes: str | None = None


class StatementImport(BaseModel):
    carrier_id: int
    statement_date: date | None = None
    reference: str | None = None
    stated_total: float | None = None
    source: str = "manual"
    lines: list[StatementLineBody] = []


class LineReconcile(BaseModel):
    commission_id: int | None = None


# Literal paths first so /commissions/report and /commissions/scan are not captured by the
# /commissions/{commission_id} int route.

@router.get("/api/v1/insurance/commissions")
def api_commission_list(policy_id: int | None = None, status: str = "", schedule: str = "",
                        principal: Principal = Depends(require_capability("insurance.commissions.read"))):
    return {"commissions": _run(lambda: com.list_commissions(
        principal, policy_id=policy_id, status=status or None, schedule=schedule or None))}


@router.get("/api/v1/insurance/commissions/report")
def api_commission_report(principal: Principal = Depends(require_capability("insurance.commissions.read"))):
    from app.services import insurance_reporting
    return _run(lambda: insurance_reporting.commission_report(principal))


@router.post("/api/v1/insurance/commissions", status_code=201)
def api_commission_create(payload: CommissionCreate, request: Request,
                          principal: Principal = Depends(require_capability("insurance.commissions.write"))):
    return _run(lambda: com.record_expected(principal, **payload.model_dump(), **_actor(request, principal)))


@router.post("/api/v1/insurance/commissions/scan")
def api_commission_scan(principal: Principal = Depends(require_capability("insurance.commissions.write"))):
    """Operational scan: surface commission variance and overdue-outstanding through the
    shared Exception Engine. Idempotent. No compliance determination."""
    from app.services import insurance_detectors
    return _run(lambda: insurance_detectors.run_insurance_commission_scan(actor_user_id=principal.user_id))


@router.get("/api/v1/insurance/commissions/{commission_id}")
def api_commission_get(commission_id: int,
                       principal: Principal = Depends(require_capability("insurance.commissions.read"))):
    return _run(lambda: com.get_commission(principal, commission_id))


@router.post("/api/v1/insurance/commissions/{commission_id}/received")
def api_commission_received(commission_id: int, payload: CommissionReceived, request: Request,
                            principal: Principal = Depends(require_capability("insurance.commissions.write"))):
    return _run(lambda: com.record_received(principal, commission_id, **payload.model_dump(),
                                            **_actor(request, principal)))


@router.post("/api/v1/insurance/commissions/{commission_id}/write-off")
def api_commission_write_off(commission_id: int, request: Request,
                             principal: Principal = Depends(require_capability("insurance.commissions.write"))):
    return _run(lambda: com.write_off(principal, commission_id, **_actor(request, principal)))


@router.post("/api/v1/insurance/policies/{policy_id}/commissions/generate", status_code=201)
def api_commission_generate(policy_id: int, payload: CommissionGenerate, request: Request,
                            principal: Principal = Depends(require_capability("insurance.commissions.write"))):
    """Fan a commission basis across the policy's active producers by split."""
    return _run(lambda: com.generate_expected(principal, policy_id=policy_id, **payload.model_dump(),
                                              **_actor(request, principal)))


@router.post("/api/v1/insurance/commission-statements", status_code=201)
def api_statement_import(payload: StatementImport, request: Request,
                         principal: Principal = Depends(require_capability("insurance.commissions.write"))):
    # model_dump() recursively renders the nested line models to plain dicts, which
    # import_statement reads with .get(); no further transformation needed.
    return _run(lambda: com.import_statement(principal, **payload.model_dump(), **_actor(request, principal)))


@router.get("/api/v1/insurance/commission-statements")
def api_statement_list(carrier_id: int | None = None, status: str = "",
                       principal: Principal = Depends(require_capability("insurance.commissions.read"))):
    return {"statements": _run(lambda: com.list_statements(
        principal, carrier_id=carrier_id, status=status or None))}


@router.get("/api/v1/insurance/commission-statements/{statement_id}")
def api_statement_get(statement_id: int,
                      principal: Principal = Depends(require_capability("insurance.commissions.read"))):
    return _run(lambda: com.get_statement(principal, statement_id))


@router.post("/api/v1/insurance/commission-statements/{statement_id}/reconcile")
def api_statement_reconcile(statement_id: int, request: Request,
                            principal: Principal = Depends(require_capability("insurance.commissions.write"))):
    return _run(lambda: com.reconcile_statement(principal, statement_id, **_actor(request, principal)))


@router.post("/api/v1/insurance/commission-lines/{line_id}/reconcile")
def api_line_reconcile(line_id: int, payload: LineReconcile, request: Request,
                       principal: Principal = Depends(require_capability("insurance.commissions.write"))):
    return _run(lambda: com.reconcile_line(principal, line_id, commission_id=payload.commission_id,
                                           **_actor(request, principal)))


@router.get("/insurance/commissions", response_class=HTMLResponse)
def console_commissions(request: Request, status: str = "",
                        principal: Principal = Depends(require_capability("insurance.commissions.read"))):
    from app.services import insurance_reporting
    commissions = _run(lambda: com.list_commissions(principal, status=status or None))
    statements = _run(lambda: com.list_statements(principal))
    report = _run(lambda: insurance_reporting.commission_report(principal))
    return templates.TemplateResponse(request=request, name="insurance/commissions.html",
                                      context={"commissions": commissions, "statements": statements,
                                               "report": report, "status": status, "principal": principal})
