"""Benefits enrollment canonical service (Release 0.9.11, Phase 2 — ADR-18).

Employment (Person ↔ Organization, **no duplicate person**), eligibility, enrollment,
waiver, termination, qualifying life events, and **retirement deferral elections**.
Employees reuse existing ``people`` records; an "employee" is a Person with a
``benefit_employments`` row for the Organization.

Authorization: ``benefits.enroll`` to mutate, ``benefits.read`` to read; record scope
resolves to the employment's Organization anchor (and the employee's person). Every
mutation writes an audit event.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import (benefit_employments, benefit_enrollments, benefit_plan_types, benefit_plans,
    benefit_plan_years, benefit_retirement_elections, engine)
from app.security.audit import write_audit_event
from app.security.authorization import benefits_in_scope

_TIERS = frozenset({"employee", "employee_spouse", "employee_children", "family", "waived"})
_ENROLL_STATUSES = frozenset({"eligible", "elected", "enrolled", "waived", "terminated", "cobra"})
_EMP_STATUSES = frozenset({"active", "terminated", "cobra", "retired"})
_SOURCES = frozenset({"staff", "portal", "import"})
_CONTRIB = frozenset({"pre_tax", "roth", "mixed", "none"})


class EnrollmentError(RuntimeError):
    """Bad input for an enrollment operation."""


class EnrollmentNotFound(EnrollmentError):
    """Employment / enrollment does not exist."""


def _rid(request_id):
    return request_id or f"benefits-{uuid.uuid4()}"


def _require(principal, capability):
    if not principal.can(capability):
        raise PermissionError(f"Missing capability: {capability}")


def _employment(c, employment_id):
    row = c.execute(select(benefit_employments.c.id, benefit_employments.c.organization_id,
                           benefit_employments.c.person_id, benefit_employments.c.employee_status)
                    .where(benefit_employments.c.id == employment_id)).mappings().one_or_none()
    if row is None:
        raise EnrollmentNotFound(f"Employment {employment_id} not found")
    return row


def _enrollment(c, enrollment_id):
    row = c.execute(
        select(benefit_enrollments.c.id, benefit_enrollments.c.plan_year_id, benefit_enrollments.c.status,
               benefit_employments.c.organization_id, benefit_employments.c.person_id, benefit_plans.c.plan_type_id)
        .select_from(benefit_enrollments
            .join(benefit_employments, benefit_employments.c.id == benefit_enrollments.c.benefit_employment_id)
            .join(benefit_plan_years, benefit_plan_years.c.id == benefit_enrollments.c.plan_year_id)
            .join(benefit_plans, benefit_plans.c.id == benefit_plan_years.c.plan_id))
        .where(benefit_enrollments.c.id == enrollment_id)).mappings().one_or_none()
    if row is None:
        raise EnrollmentNotFound(f"Enrollment {enrollment_id} not found")
    return row


def _scope(principal, *, organization_id, person_id, write, connection):
    if not benefits_in_scope(principal, organization_id=organization_id, person_id=person_id,
                             write=write, connection=connection):
        raise PermissionError("Record is outside your record scope")


# --- employment / roster -----------------------------------------------------

def create_employment(principal, *, organization_id, person_id, hire_date=None,
                      benefit_class=None, employee_status="active", request_id=None):
    _require(principal, "benefits.enroll")
    if employee_status not in _EMP_STATUSES:
        raise EnrollmentError(f"Unsupported employee status: {employee_status}")
    with engine.begin() as c:
        _scope(principal, organization_id=organization_id, person_id=person_id, write=True, connection=c)
        try:
            emp_id = c.execute(benefit_employments.insert().values(
                person_id=person_id, organization_id=organization_id, employee_status=employee_status,
                hire_date=hire_date, benefit_class=benefit_class
            ).returning(benefit_employments.c.id)).scalar_one()
        except IntegrityError:
            raise EnrollmentError("This person is already an employee of the organization")
    write_audit_event(action="benefit.employment.created", entity_type="benefit_employment", entity_id=emp_id,
                      actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"organization_id": organization_id, "person_id": person_id})
    return emp_id


def terminate_employment(employment_id, *, principal, termination_date=None, request_id=None):
    _require(principal, "benefits.enroll")
    with engine.begin() as c:
        emp = _employment(c, employment_id)
        _scope(principal, organization_id=emp["organization_id"], person_id=emp["person_id"], write=True, connection=c)
        c.execute(benefit_employments.update().where(benefit_employments.c.id == employment_id)
                  .values(employee_status="terminated", termination_date=termination_date))
        # end active coverage for this employee
        c.execute(benefit_enrollments.update()
                  .where(benefit_enrollments.c.benefit_employment_id == employment_id,
                         benefit_enrollments.c.status.in_(("eligible", "elected", "enrolled")))
                  .values(status="terminated", end_date=termination_date))
    write_audit_event(action="benefit.employment.terminated", entity_type="benefit_employment",
                      entity_id=employment_id, actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"termination_date": str(termination_date) if termination_date else None})


# --- enrollment / eligibility / waiver ---------------------------------------

def enroll(principal, *, benefit_employment_id, plan_year_id, coverage_tier="employee",
           status="elected", source="staff", effective_date=None, request_id=None):
    _require(principal, "benefits.enroll")
    if coverage_tier not in _TIERS:
        raise EnrollmentError(f"Unsupported coverage tier: {coverage_tier}")
    if status not in _ENROLL_STATUSES:
        raise EnrollmentError(f"Unsupported enrollment status: {status}")
    if source not in _SOURCES:
        raise EnrollmentError(f"Unsupported source: {source}")
    elected_at = datetime.now(timezone.utc) if status in ("elected", "enrolled") else None
    with engine.begin() as c:
        emp = _employment(c, benefit_employment_id)
        _scope(principal, organization_id=emp["organization_id"], person_id=emp["person_id"], write=True, connection=c)
        try:
            enr_id = c.execute(benefit_enrollments.insert().values(
                benefit_employment_id=benefit_employment_id, plan_year_id=plan_year_id,
                coverage_tier=coverage_tier, status=status, source=source,
                effective_date=effective_date, elected_at=elected_at
            ).returning(benefit_enrollments.c.id)).scalar_one()
        except IntegrityError:
            raise EnrollmentError("An enrollment already exists for this employee and plan year")
    write_audit_event(action="benefit.enrollment.created", entity_type="benefit_enrollment", entity_id=enr_id,
                      actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"coverage_tier": coverage_tier, "status": status, "source": source})
    return enr_id


def set_enrollment_status(enrollment_id, status, *, principal, coverage_tier=None, end_date=None,
                          effective_date=None, request_id=None, _action="benefit.enrollment.updated"):
    _require(principal, "benefits.enroll")
    if status not in _ENROLL_STATUSES:
        raise EnrollmentError(f"Unsupported enrollment status: {status}")
    if coverage_tier is not None and coverage_tier not in _TIERS:
        raise EnrollmentError(f"Unsupported coverage tier: {coverage_tier}")
    with engine.begin() as c:
        row = _enrollment(c, enrollment_id)
        _scope(principal, organization_id=row["organization_id"], person_id=row["person_id"], write=True, connection=c)
        values = {"status": status}
        if coverage_tier is not None:
            values["coverage_tier"] = coverage_tier
        if end_date is not None:
            values["end_date"] = end_date
        if effective_date is not None:
            values["effective_date"] = effective_date
        c.execute(benefit_enrollments.update().where(benefit_enrollments.c.id == enrollment_id).values(**values))
    write_audit_event(action=_action, entity_type="benefit_enrollment", entity_id=enrollment_id,
                      actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"from": row["status"], "to": status})
    return enrollment_id


def waive(enrollment_id, *, principal, request_id=None):
    return set_enrollment_status(enrollment_id, "waived", principal=principal, coverage_tier="waived",
                                 request_id=request_id, _action="benefit.enrollment.waived")


def terminate_enrollment(enrollment_id, *, principal, end_date=None, request_id=None):
    return set_enrollment_status(enrollment_id, "terminated", principal=principal, end_date=end_date,
                                 request_id=request_id, _action="benefit.enrollment.terminated")


def qualifying_life_event(principal, *, benefit_employment_id, plan_year_id, event_type,
                          coverage_tier, effective_date, request_id=None):
    """Record a mid-year qualifying life event: (re)elect coverage at the new tier. Upserts the
    employee's enrollment for the plan year and audits the QLE."""
    _require(principal, "benefits.enroll")
    if coverage_tier not in _TIERS:
        raise EnrollmentError(f"Unsupported coverage tier: {coverage_tier}")
    with engine.begin() as c:
        emp = _employment(c, benefit_employment_id)
        _scope(principal, organization_id=emp["organization_id"], person_id=emp["person_id"], write=True, connection=c)
        existing = c.scalar(select(benefit_enrollments.c.id).where(
            benefit_enrollments.c.benefit_employment_id == benefit_employment_id,
            benefit_enrollments.c.plan_year_id == plan_year_id))
        if existing:
            c.execute(benefit_enrollments.update().where(benefit_enrollments.c.id == existing).values(
                status="elected", coverage_tier=coverage_tier, effective_date=effective_date,
                elected_at=datetime.now(timezone.utc), source="staff"))
            enr_id = existing
        else:
            enr_id = c.execute(benefit_enrollments.insert().values(
                benefit_employment_id=benefit_employment_id, plan_year_id=plan_year_id,
                coverage_tier=coverage_tier, status="elected", source="staff",
                effective_date=effective_date, elected_at=datetime.now(timezone.utc)
            ).returning(benefit_enrollments.c.id)).scalar_one()
    write_audit_event(action="benefit.qle.recorded", entity_type="benefit_enrollment", entity_id=enr_id,
                      actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"event_type": event_type, "coverage_tier": coverage_tier,
                                "effective_date": str(effective_date)})
    return enr_id


# --- retirement deferral election --------------------------------------------

def set_retirement_election(enrollment_id, *, principal, deferral_percent=None, roth_percent=None,
                            contribution_type="none", auto_enrolled=False, effective_date=None,
                            request_id=None):
    """Create/replace the 1:1 retirement deferral election for an enrollment on a retirement plan.
    Validates percentages and that the enrollment is on a retirement plan."""
    _require(principal, "benefits.enroll")
    if contribution_type not in _CONTRIB:
        raise EnrollmentError(f"Unsupported contribution type: {contribution_type}")
    for pct, label in ((deferral_percent, "deferral_percent"), (roth_percent, "roth_percent")):
        if pct is not None and not (0 <= float(pct) <= 100):
            raise EnrollmentError(f"{label} must be between 0 and 100")
    if (deferral_percent is not None and roth_percent is not None
            and float(roth_percent) > float(deferral_percent)):
        raise EnrollmentError("roth_percent cannot exceed deferral_percent")
    with engine.begin() as c:
        row = _enrollment(c, enrollment_id)
        _scope(principal, organization_id=row["organization_id"], person_id=row["person_id"], write=True, connection=c)
        loc = c.scalar(select(benefit_plan_types.c.line_of_coverage)
                       .where(benefit_plan_types.c.id == row["plan_type_id"]))
        if loc != "retirement":
            raise EnrollmentError("Retirement elections apply only to retirement-plan enrollments")
        values = dict(deferral_percent=deferral_percent, roth_percent=roth_percent,
                      contribution_type=contribution_type, auto_enrolled=auto_enrolled,
                      effective_date=effective_date)
        existing = c.scalar(select(benefit_retirement_elections.c.id)
                            .where(benefit_retirement_elections.c.benefit_enrollment_id == enrollment_id))
        if existing:
            c.execute(benefit_retirement_elections.update()
                      .where(benefit_retirement_elections.c.id == existing).values(**values))
            election_id = existing
        else:
            election_id = c.execute(benefit_retirement_elections.insert()
                                    .values(benefit_enrollment_id=enrollment_id, **values)
                                    .returning(benefit_retirement_elections.c.id)).scalar_one()
    write_audit_event(action="benefit.retirement_election.set", entity_type="benefit_enrollment",
                      entity_id=enrollment_id, actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"deferral_percent": (float(deferral_percent) if deferral_percent is not None else None),
                                "contribution_type": contribution_type, "auto_enrolled": auto_enrolled})
    return election_id


def get_enrollment(enrollment_id, *, principal):
    _require(principal, "benefits.read")
    with engine.connect() as c:
        row = _enrollment(c, enrollment_id)
        _scope(principal, organization_id=row["organization_id"], person_id=row["person_id"], write=False, connection=c)
        data = dict(c.execute(select(benefit_enrollments)
                    .where(benefit_enrollments.c.id == enrollment_id)).mappings().one())
        election = c.execute(select(benefit_retirement_elections)
                             .where(benefit_retirement_elections.c.benefit_enrollment_id == enrollment_id)).mappings().one_or_none()
        data["retirement_election"] = dict(election) if election else None
        return data
