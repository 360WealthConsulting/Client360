"""Benefits domain canonical service (Release 0.9.11, Phase 2 — ADR-18).

Benefit and retirement **plans**, **plan years**, and **retirement plan details** anchored
to an Organization. Retirement is first-class (a plan whose type is on the ``retirement``
line of coverage carries a 1:1 ``benefit_retirement_plan_details`` row). Provider-neutral:
plans reference a ``benefit_providers`` row (Betterment first) by id — no integration.

Authorization: ``benefits.write`` to mutate, ``benefits.read`` to read; record scope resolves
to the plan's Organization anchor. Every mutation writes an audit event.
"""
import uuid

from sqlalchemy import select

from app.db import (benefit_plan_types, benefit_plan_years, benefit_plans, benefit_providers,
    benefit_retirement_plan_details, engine)
from app.security.audit import write_audit_event
from app.security.authorization import organization_in_scope

_PLAN_STATUSES = frozenset({"draft", "active", "renewing", "terminated"})
_YEAR_STATUSES = frozenset({"upcoming", "open_enrollment", "active", "closed"})
_FUNDING = frozenset({"fully_insured", "level_funded", "self_funded", "trustee", "custodial"})
_SAFE_HARBOR = frozenset({"none", "basic_match", "enhanced_match", "nonelective"})
_FIDUCIARY = frozenset({"3(21)", "3(38)", "none"})


class BenefitsError(RuntimeError):
    """Bad input for a benefits domain operation."""


class BenefitsNotFound(BenefitsError):
    """Plan / plan year does not exist."""


def _rid(request_id):
    return request_id or f"benefits-{uuid.uuid4()}"


def _require(principal, capability):
    if not principal.can(capability):
        raise PermissionError(f"Missing capability: {capability}")


def _require_scope(principal, organization_id, *, write, connection):
    if not organization_in_scope(principal, organization_id, write=write, connection=connection):
        raise PermissionError("Plan is outside your record scope")


def _plan_type(c, code):
    row = c.execute(select(benefit_plan_types.c.id, benefit_plan_types.c.line_of_coverage)
                    .where(benefit_plan_types.c.code == code)).mappings().one_or_none()
    if row is None:
        raise BenefitsError(f"Unknown plan type: {code}")
    return row


def _provider_id(c, code):
    if code is None:
        return None
    pid = c.scalar(select(benefit_providers.c.id).where(benefit_providers.c.code == code))
    if pid is None:
        raise BenefitsError(f"Unknown provider: {code}")
    return pid


def _plan_row(c, plan_id):
    row = c.execute(select(benefit_plans.c.id, benefit_plans.c.organization_id, benefit_plans.c.plan_type_id,
                           benefit_plans.c.status).where(benefit_plans.c.id == plan_id)).mappings().one_or_none()
    if row is None:
        raise BenefitsNotFound(f"Plan {plan_id} not found")
    return row


def _is_retirement(c, plan_type_id):
    loc = c.scalar(select(benefit_plan_types.c.line_of_coverage).where(benefit_plan_types.c.id == plan_type_id))
    return loc == "retirement"


# --- plans -------------------------------------------------------------------

def create_plan(principal, *, organization_id, plan_type_code, name, provider_code=None,
                funding_type=None, engagement_id=None, effective_date=None, renewal_date=None,
                status="draft", request_id=None):
    _require(principal, "benefits.write")
    if status not in _PLAN_STATUSES:
        raise BenefitsError(f"Unsupported plan status: {status}")
    if funding_type is not None and funding_type not in _FUNDING:
        raise BenefitsError(f"Unsupported funding type: {funding_type}")
    if not (name or "").strip():
        raise BenefitsError("Plan name is required")
    with engine.begin() as c:
        _require_scope(principal, organization_id, write=True, connection=c)
        ptype = _plan_type(c, plan_type_code)
        provider_id = _provider_id(c, provider_code)
        plan_id = c.execute(benefit_plans.insert().values(
            organization_id=organization_id, plan_type_id=ptype["id"], provider_id=provider_id,
            engagement_id=engagement_id, name=name.strip(), funding_type=funding_type,
            status=status, effective_date=effective_date, renewal_date=renewal_date
        ).returning(benefit_plans.c.id)).scalar_one()
    write_audit_event(action="benefit.plan.created", entity_type="benefit_plan", entity_id=plan_id,
                      actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"organization_id": organization_id, "plan_type": plan_type_code,
                                "line_of_coverage": ptype["line_of_coverage"], "provider": provider_code})
    return get_plan(plan_id, principal=principal)


def get_plan(plan_id, *, principal, connection=None):
    _require(principal, "benefits.read")

    def _load(c):
        row = c.execute(select(benefit_plans).where(benefit_plans.c.id == plan_id)).mappings().one_or_none()
        if row is None:
            raise BenefitsNotFound(f"Plan {plan_id} not found")
        _require_scope(principal, row["organization_id"], write=False, connection=c)
        data = dict(row)
        details = c.execute(select(benefit_retirement_plan_details)
                            .where(benefit_retirement_plan_details.c.plan_id == plan_id)).mappings().one_or_none()
        data["retirement_details"] = dict(details) if details else None
        return data

    if connection is not None:
        return _load(connection)
    with engine.connect() as c:
        return _load(c)


def update_plan_status(plan_id, status, *, principal, request_id=None):
    _require(principal, "benefits.write")
    if status not in _PLAN_STATUSES:
        raise BenefitsError(f"Unsupported plan status: {status}")
    with engine.begin() as c:
        row = _plan_row(c, plan_id)
        _require_scope(principal, row["organization_id"], write=True, connection=c)
        c.execute(benefit_plans.update().where(benefit_plans.c.id == plan_id).values(status=status))
    write_audit_event(action="benefit.plan.status_changed", entity_type="benefit_plan", entity_id=plan_id,
                      actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"from": row["status"], "to": status})
    return get_plan(plan_id, principal=principal)


def list_plans(organization_id, *, principal):
    _require(principal, "benefits.read")
    with engine.connect() as c:
        _require_scope(principal, organization_id, write=False, connection=c)
        rows = c.execute(select(benefit_plans).where(benefit_plans.c.organization_id == organization_id)
                         .order_by(benefit_plans.c.id)).mappings().all()
    return [dict(r) for r in rows]


# --- plan years --------------------------------------------------------------

def create_plan_year(plan_id, *, principal, plan_year, effective_date=None, renewal_date=None,
                     open_enrollment_start=None, open_enrollment_end=None, status="upcoming",
                     request_id=None):
    _require(principal, "benefits.write")
    if status not in _YEAR_STATUSES:
        raise BenefitsError(f"Unsupported plan-year status: {status}")
    from sqlalchemy.exc import IntegrityError
    with engine.begin() as c:
        row = _plan_row(c, plan_id)
        _require_scope(principal, row["organization_id"], write=True, connection=c)
        try:
            year_id = c.execute(benefit_plan_years.insert().values(
                plan_id=plan_id, plan_year=int(plan_year), effective_date=effective_date,
                renewal_date=renewal_date, open_enrollment_start=open_enrollment_start,
                open_enrollment_end=open_enrollment_end, status=status
            ).returning(benefit_plan_years.c.id)).scalar_one()
        except IntegrityError:
            raise BenefitsError(f"Plan year {plan_year} already exists for this plan")
    write_audit_event(action="benefit.plan_year.created", entity_type="benefit_plan", entity_id=plan_id,
                      actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"plan_year": int(plan_year), "status": status})
    return year_id


def set_plan_year_status(plan_year_id, status, *, principal, request_id=None):
    _require(principal, "benefits.write")
    if status not in _YEAR_STATUSES:
        raise BenefitsError(f"Unsupported plan-year status: {status}")
    with engine.begin() as c:
        row = c.execute(select(benefit_plan_years.c.plan_id, benefit_plans.c.organization_id)
                        .select_from(benefit_plan_years.join(benefit_plans,
                            benefit_plans.c.id == benefit_plan_years.c.plan_id))
                        .where(benefit_plan_years.c.id == plan_year_id)).mappings().one_or_none()
        if row is None:
            raise BenefitsNotFound(f"Plan year {plan_year_id} not found")
        _require_scope(principal, row["organization_id"], write=True, connection=c)
        c.execute(benefit_plan_years.update().where(benefit_plan_years.c.id == plan_year_id).values(status=status))
    write_audit_event(action="benefit.plan_year.status_changed", entity_type="benefit_plan",
                      entity_id=row["plan_id"], actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"plan_year_id": plan_year_id, "to": status})


def list_plan_years(plan_id, *, principal):
    _require(principal, "benefits.read")
    with engine.connect() as c:
        row = _plan_row(c, plan_id)
        _require_scope(principal, row["organization_id"], write=False, connection=c)
        return [dict(r) for r in c.execute(select(benefit_plan_years)
                .where(benefit_plan_years.c.plan_id == plan_id)
                .order_by(benefit_plan_years.c.plan_year)).mappings()]


# --- retirement plan details -------------------------------------------------

def set_retirement_details(plan_id, *, principal, provider_code=None, safe_harbor_type="none",
                           match_formula=None, auto_enrollment=False, auto_enroll_default_percent=None,
                           vesting_schedule=None, eligibility_rule=None, fiduciary_role="none",
                           erisa=True, adoption_agreement_document_id=None, request_id=None):
    """Create/replace the 1:1 retirement detail for a retirement plan. Rejects non-retirement plans."""
    _require(principal, "benefits.write")
    if safe_harbor_type not in _SAFE_HARBOR:
        raise BenefitsError(f"Unsupported safe_harbor_type: {safe_harbor_type}")
    if fiduciary_role not in _FIDUCIARY:
        raise BenefitsError(f"Unsupported fiduciary_role: {fiduciary_role}")
    with engine.begin() as c:
        row = _plan_row(c, plan_id)
        _require_scope(principal, row["organization_id"], write=True, connection=c)
        if not _is_retirement(c, row["plan_type_id"]):
            raise BenefitsError("Retirement details apply only to retirement plans")
        provider_id = _provider_id(c, provider_code)
        values = dict(provider_id=provider_id, safe_harbor_type=safe_harbor_type,
                      match_formula=match_formula, auto_enrollment=auto_enrollment,
                      auto_enroll_default_percent=auto_enroll_default_percent,
                      vesting_schedule=vesting_schedule, eligibility_rule=eligibility_rule or {},
                      fiduciary_role=fiduciary_role, erisa=erisa,
                      adoption_agreement_document_id=adoption_agreement_document_id)
        existing = c.scalar(select(benefit_retirement_plan_details.c.id)
                            .where(benefit_retirement_plan_details.c.plan_id == plan_id))
        if existing:
            c.execute(benefit_retirement_plan_details.update()
                      .where(benefit_retirement_plan_details.c.id == existing).values(**values))
            detail_id = existing
        else:
            detail_id = c.execute(benefit_retirement_plan_details.insert()
                                  .values(plan_id=plan_id, **values)
                                  .returning(benefit_retirement_plan_details.c.id)).scalar_one()
    write_audit_event(action="benefit.retirement_details.set", entity_type="benefit_plan", entity_id=plan_id,
                      actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"safe_harbor_type": safe_harbor_type, "fiduciary_role": fiduciary_role,
                                "auto_enrollment": auto_enrollment})
    return detail_id


def list_providers():
    """Reference list of provider-neutral providers (Betterment seeded). No scope — reference data."""
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(benefit_providers)
                .order_by(benefit_providers.c.provider_type, benefit_providers.c.code)).mappings()]
