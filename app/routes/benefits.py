"""Organization & Employee Benefits staff API + consoles (Release 0.9.11, Phase 6 — ADR-18).

Thin HTTP layer over the Phase 2–5 canonical services — **no business logic here.** Middleware
gates `/organizations` + `/api/v1/organizations` as `organization.*` and `/benefits` +
`/api/v1/benefits` as `benefits.*` (with the `.read→.write` inference); the services enforce
the finer `benefits.enroll` / `benefits.compliance` / `benefits.sensitive.read` and
Organization record scope. Consoles render on the modern Client360 shell and show **names, not
raw IDs**. Benefits exceptions are viewed through the existing `/exceptions?domain=benefits`;
the employer portal (Phase 7) and dashboards/reporting (Phase 8) are out of scope here.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select

from app.db import engine, people, relationship_entities, users
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import benefits_domain as bd
from app.services import benefits_enrollment as be
from app.services import benefits_obligations as ob
from app.services import benefits_reporting as br
from app.services import engagement_service as es
from app.services import organization_service as org

router = APIRouter(tags=["benefits"])
templates = Jinja2Templates(directory="app/templates")


# --- error translation -------------------------------------------------------

_NOT_FOUND = (org.OrganizationNotFound, bd.BenefitsNotFound, be.EnrollmentNotFound,
              es.EngagementNotFound, ob.ObligationNotFound)
_BAD_INPUT = (org.OrganizationError, bd.BenefitsError, be.EnrollmentError,
              es.EngagementError, ob.ObligationError)


def _run(fn):
    """Call a canonical service and translate its errors to HTTP responses."""
    try:
        return fn()
    except _NOT_FOUND:
        raise HTTPException(404, "Not found")
    except PermissionError as exc:
        # out-of-scope hides existence (404); a missing capability is 403
        raise HTTPException(403 if "capability" in str(exc).lower() else 404, str(exc))
    except _BAD_INPUT as exc:
        raise HTTPException(400, str(exc))


# --- request models ----------------------------------------------------------

class OrgCreate(BaseModel):
    name: str
    entity_type: str = "business"
    legal_name: Optional[str] = None
    ein: Optional[str] = None
    industry: Optional[str] = None
    entity_form: Optional[str] = None
    renewal_month: Optional[int] = None
    status: str = "prospect"


class OrgUpdate(BaseModel):
    legal_name: Optional[str] = None
    industry: Optional[str] = None
    entity_form: Optional[str] = None
    renewal_month: Optional[int] = None
    status: Optional[str] = None
    ein: Optional[str] = None


class ServiceLineBody(BaseModel):
    service_line_code: str
    status: str = "active"


class RoleBody(BaseModel):
    user_id: int
    role_code: str
    service_line_code: Optional[str] = None
    is_primary: bool = False


class OwnershipBody(BaseModel):
    owner_person_id: Optional[int] = None
    owner_household_id: Optional[int] = None
    owner_organization_id: Optional[int] = None
    relationship_code: str = "owns"
    ownership_percentage: Optional[float] = None
    voting_percentage: Optional[float] = None
    ownership_type: Optional[str] = None
    is_direct: bool = True


class EngagementBody(BaseModel):
    service_line_code: str
    engagement_type: str
    title: Optional[str] = None
    due_date: Optional[date] = None


class PlanBody(BaseModel):
    plan_type_code: str
    name: str
    provider_code: Optional[str] = None
    funding_type: Optional[str] = None
    effective_date: Optional[date] = None
    renewal_date: Optional[date] = None


class PlanYearBody(BaseModel):
    plan_year: int
    status: str = "upcoming"
    effective_date: Optional[date] = None
    renewal_date: Optional[date] = None
    open_enrollment_start: Optional[date] = None
    open_enrollment_end: Optional[date] = None


class EmploymentBody(BaseModel):
    person_id: int
    hire_date: Optional[date] = None
    benefit_class: Optional[str] = None


class EnrollmentBody(BaseModel):
    benefit_employment_id: int
    plan_year_id: int
    coverage_tier: str = "employee"
    status: str = "elected"


class ObligationBody(BaseModel):
    obligation_type: str
    due_date: date
    title: Optional[str] = None
    service_line_code: Optional[str] = None
    engagement_id: Optional[int] = None
    plan_id: Optional[int] = None
    plan_year_id: Optional[int] = None
    warning_days: Optional[int] = None
    recurrence: str = "one_time"
    responsible_role: Optional[str] = None
    notes: Optional[str] = None


# --- name resolution (console shows names, not raw IDs) ----------------------

def _people_names(ids):
    ids = [i for i in ids if i]
    if not ids:
        return {}
    with engine.connect() as c:
        return {r["id"]: r["full_name"] for r in c.execute(
            select(people.c.id, people.c.full_name).where(people.c.id.in_(ids))).mappings()}


def _user_names(ids):
    ids = [i for i in ids if i]
    if not ids:
        return {}
    with engine.connect() as c:
        return {r["id"]: r["display_name"] for r in c.execute(
            select(users.c.id, users.c.display_name).where(users.c.id.in_(ids))).mappings()}


def _entity_names(ids):
    ids = [i for i in ids if i]
    if not ids:
        return {}
    with engine.connect() as c:
        return {r["id"]: r["name"] for r in c.execute(
            select(relationship_entities.c.id, relationship_entities.c.name)
            .where(relationship_entities.c.id.in_(ids))).mappings()}


# --- Organizations JSON API (/api/v1/organizations) --------------------------

@router.get("/api/v1/organizations")
def api_org_list(status: str = "", principal: Principal = Depends(require_capability("organization.read"))):
    return {"organizations": _run(lambda: org.list_organizations(principal, status=status or None))}


@router.post("/api/v1/organizations", status_code=201)
def api_org_create(payload: OrgCreate, principal: Principal = Depends(require_capability("organization.write"))):
    return _run(lambda: org.create_organization(principal, **payload.dict()))


@router.get("/api/v1/organizations/{organization_id}")
def api_org_get(organization_id: int, include_sensitive: bool = False,
                principal: Principal = Depends(require_capability("organization.read"))):
    return _run(lambda: org.get_organization(organization_id, principal=principal,
                                             include_sensitive=include_sensitive))


@router.patch("/api/v1/organizations/{organization_id}")
def api_org_update(organization_id: int, payload: OrgUpdate,
                   principal: Principal = Depends(require_capability("organization.write"))):
    return _run(lambda: org.update_organization(organization_id, principal=principal,
                                                **{k: v for k, v in payload.dict().items() if v is not None}))


@router.get("/api/v1/organizations/{organization_id}/service-lines")
def api_org_service_lines(organization_id: int, principal: Principal = Depends(require_capability("organization.read"))):
    return {"service_lines": _run(lambda: org.list_service_lines(organization_id, principal=principal))}


@router.post("/api/v1/organizations/{organization_id}/service-lines", status_code=201)
def api_org_add_service_line(organization_id: int, payload: ServiceLineBody,
                             principal: Principal = Depends(require_capability("organization.write"))):
    sid = _run(lambda: org.add_service_line(organization_id, payload.service_line_code,
                                            principal=principal, status=payload.status))
    return {"id": sid}


@router.get("/api/v1/organizations/{organization_id}/roles")
def api_org_roles(organization_id: int, principal: Principal = Depends(require_capability("organization.read"))):
    return {"roles": _run(lambda: org.list_roles(organization_id, principal=principal))}


@router.post("/api/v1/organizations/{organization_id}/roles", status_code=201)
def api_org_add_role(organization_id: int, payload: RoleBody,
                     principal: Principal = Depends(require_capability("organization.write"))):
    rid = _run(lambda: org.assign_role(organization_id, principal=principal, user_id=payload.user_id,
                                       role_code=payload.role_code, service_line_code=payload.service_line_code,
                                       is_primary=payload.is_primary))
    return {"id": rid}


@router.get("/api/v1/organizations/{organization_id}/owners")
def api_org_owners(organization_id: int, principal: Principal = Depends(require_capability("organization.read"))):
    return {"owners": _run(lambda: org.list_owners(organization_id, principal=principal))}


@router.post("/api/v1/organizations/{organization_id}/ownership", status_code=201)
def api_org_ownership(organization_id: int, payload: OwnershipBody,
                      principal: Principal = Depends(require_capability("organization.write"))):
    return _run(lambda: org.record_ownership(principal=principal, owned_organization_id=organization_id,
                                            **payload.dict()))


@router.get("/api/v1/organizations/{organization_id}/engagements")
def api_org_engagements(organization_id: int, principal: Principal = Depends(require_capability("organization.read"))):
    return {"engagements": _run(lambda: es.list_engagements(principal, organization_id=organization_id))}


@router.post("/api/v1/organizations/{organization_id}/engagements", status_code=201)
def api_org_create_engagement(organization_id: int, payload: EngagementBody,
                              principal: Principal = Depends(require_capability("organization.write"))):
    return _run(lambda: es.create_engagement(principal, organization_id=organization_id, **payload.dict()))


# --- Benefits JSON API (/api/v1/benefits) ------------------------------------

@router.get("/api/v1/benefits/providers")
def api_providers(principal: Principal = Depends(require_capability("benefits.read"))):
    return {"providers": bd.list_providers()}


@router.get("/api/v1/benefits/report")
def api_benefits_report(principal: Principal = Depends(require_capability("benefits.read"))):
    return _run(lambda: br.benefits_report(principal))


@router.get("/api/v1/benefits/organizations/{organization_id}/plans")
def api_plans(organization_id: int, principal: Principal = Depends(require_capability("benefits.read"))):
    return {"plans": _run(lambda: bd.list_plans(organization_id, principal=principal))}


@router.post("/api/v1/benefits/organizations/{organization_id}/plans", status_code=201)
def api_create_plan(organization_id: int, payload: PlanBody,
                    principal: Principal = Depends(require_capability("benefits.write"))):
    return _run(lambda: bd.create_plan(principal, organization_id=organization_id, **payload.dict()))


@router.get("/api/v1/benefits/plans/{plan_id}")
def api_plan(plan_id: int, principal: Principal = Depends(require_capability("benefits.read"))):
    return _run(lambda: bd.get_plan(plan_id, principal=principal))


@router.post("/api/v1/benefits/plans/{plan_id}/plan-years", status_code=201)
def api_create_plan_year(plan_id: int, payload: PlanYearBody,
                         principal: Principal = Depends(require_capability("benefits.write"))):
    return {"id": _run(lambda: bd.create_plan_year(plan_id, principal=principal, **payload.dict()))}


@router.get("/api/v1/benefits/plans/{plan_id}/plan-years")
def api_plan_years(plan_id: int, principal: Principal = Depends(require_capability("benefits.read"))):
    return {"plan_years": _run(lambda: bd.list_plan_years(plan_id, principal=principal))}


@router.post("/api/v1/benefits/organizations/{organization_id}/employments", status_code=201)
def api_create_employment(organization_id: int, payload: EmploymentBody,
                          principal: Principal = Depends(require_capability("benefits.write"))):
    return {"id": _run(lambda: be.create_employment(principal, organization_id=organization_id, **payload.dict()))}


@router.post("/api/v1/benefits/enrollments", status_code=201)
def api_create_enrollment(payload: EnrollmentBody,
                          principal: Principal = Depends(require_capability("benefits.write"))):
    return {"id": _run(lambda: be.enroll(principal, **payload.dict()))}


@router.get("/api/v1/benefits/organizations/{organization_id}/obligations")
def api_obligations(organization_id: int, status: str = "",
                    principal: Principal = Depends(require_capability("benefits.read"))):
    return {"obligations": _run(lambda: ob.list_obligations(principal, organization_id, status=status or None))}


@router.post("/api/v1/benefits/organizations/{organization_id}/obligations", status_code=201)
def api_create_obligation(organization_id: int, payload: ObligationBody,
                          principal: Principal = Depends(require_capability("benefits.write"))):
    return _run(lambda: ob.create_obligation(principal, organization_id=organization_id, **payload.dict()))


@router.post("/api/v1/benefits/obligations/{obligation_id}/complete")
def api_complete_obligation(obligation_id: int,
                            principal: Principal = Depends(require_capability("benefits.write"))):
    return _run(lambda: ob.complete_obligation(obligation_id, principal=principal))


@router.post("/api/v1/benefits/obligations/{obligation_id}/{action}")
def api_obligation_status(obligation_id: int, action: str,
                          principal: Principal = Depends(require_capability("benefits.write"))):
    if action not in ("cancel", "waive"):
        raise HTTPException(404, "Unknown action")
    status = "cancelled" if action == "cancel" else "waived"
    return _run(lambda: ob.set_status(obligation_id, status, principal=principal))


# --- HTML consoles (modern shell) --------------------------------------------

@router.get("/organizations", response_class=HTMLResponse)
def console_org_list(request: Request, status: str = "",
                     principal: Principal = Depends(require_capability("organization.read"))):
    rows = _run(lambda: org.list_organizations(principal, status=status or None))
    return templates.TemplateResponse(request=request, name="organizations/list.html",
        context={"organizations": rows, "filter_status": status, "principal": principal})


@router.get("/organizations/{organization_id}", response_class=HTMLResponse)
def console_org_detail(organization_id: int, request: Request,
                       principal: Principal = Depends(require_capability("organization.read"))):
    data = _run(lambda: org.get_organization(organization_id, principal=principal,
                include_sensitive=principal.can("benefits.sensitive.read")))
    ctx = {"org": data, "principal": principal, "organization_id": organization_id,
           "service_lines": _run(lambda: org.list_service_lines(organization_id, principal=principal)),
           "roles": org.list_roles(organization_id, principal=principal),
           "owners": org.list_owners(organization_id, principal=principal),
           "engagements": es.list_engagements(principal, organization_id=organization_id)}
    # benefits sections only for benefits.read holders
    if principal.can("benefits.read"):
        ctx["plans"] = bd.list_plans(organization_id, principal=principal)
        ctx["obligations"] = ob.list_obligations(principal, organization_id)
    else:
        ctx["plans"], ctx["obligations"] = None, None
    # resolve display names
    ctx["role_users"] = _user_names([r["user_id"] for r in ctx["roles"]])
    ctx["owner_names"] = _entity_names([o["owner_entity_id"] for o in ctx["owners"]])
    return templates.TemplateResponse(request=request, name="organizations/detail.html", context=ctx)


@router.get("/benefits/reporting", response_class=HTMLResponse)
def console_benefits_reporting(request: Request,
                              principal: Principal = Depends(require_capability("benefits.read"))):
    # Registered before /benefits so "reporting" is not treated as an employer path.
    report = _run(lambda: br.benefits_report(principal))
    return templates.TemplateResponse(request=request, name="benefits/reporting.html",
        context={"report": report, "principal": principal})


@router.get("/benefits", response_class=HTMLResponse)
def console_benefits_list(request: Request,
                          principal: Principal = Depends(require_capability("benefits.read"))):
    # Employers = organizations the principal can see that have a benefits/retirement service line.
    orgs = org.list_organizations(principal) if principal.can("organization.read") else []
    employers = []
    for o in orgs:
        lines = {sl["code"] for sl in _run(lambda oid=o["id"]: org.list_service_lines(oid, principal=principal))}
        if lines & {"benefits", "retirement"}:
            employers.append({**o, "lines": sorted(lines & {"benefits", "retirement"})})
    return templates.TemplateResponse(request=request, name="benefits/list.html",
        context={"employers": employers, "principal": principal,
                 "needs_org_read": not principal.can("organization.read")})
