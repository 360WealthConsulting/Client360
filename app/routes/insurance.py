"""Insurance Operations HTTP layer (Release 0.10.0, Phase 1).

Thin HTTP over app.services.insurance — no business logic here. Exposes the JSON
API under /api/v1/insurance and staff HTML consoles under /insurance. Capability
gating is per-endpoint; record scope is enforced in the service.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import insurance as ins
from app.templating import templates

router = APIRouter(tags=["insurance"])

_NOT_FOUND = (ins.InsuranceNotFound,)
_BAD_INPUT = (ins.InsuranceError,)


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
def api_party_add(policy_id: int, payload: PartyBody,
                  principal: Principal = Depends(require_capability("insurance.write"))):
    return _run(lambda: ins.add_party(principal, policy_id, **payload.model_dump()))


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
