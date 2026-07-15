"""Release 0.9.11 — Benefits & retirement exception detectors (Phase 3) tests.

Detectors are exercised end to end through the Exception Engine. Scans run globally, so
assertions filter to a freshly created Organization (via ``related_entity_id``) to stay
deterministic on the shared test database. Covers every active detector, stable dedupe keys,
no-duplicate rescans, auto-resolution, reopen/recurrence, Organization/related-entity
linkage, health-vs-retirement handling, cross-organization isolation + authorization, inert
disabled-integration types, no-sensitive-data-leakage, and audit + immutable events.
"""
import os
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.db import (audit_events, benefit_document_links, benefit_employments, benefit_enrollments,
    documents, engine, exception_events, exception_types, exceptions, households, people, users)
from app.security import benefits_crypto
from app.security.models import Principal
from app.services import benefits_detectors as det
from app.services import benefits_domain as bd
from app.services import benefits_enrollment as be
from app.services import exception_engine as ee
from app.services import organization_service as org

os.environ.setdefault("BENEFITS_FIELD_KEY", benefits_crypto.generate_key())
TODAY = date.today()

FULL = frozenset({"organization.read", "organization.write", "benefits.read", "benefits.write",
                  "benefits.enroll", "benefits.compliance", "benefits.sensitive.read",
                  "exception.read", "exception.write", "record.read_all", "record.write_all"})
SCOPED = frozenset({"organization.read", "organization.write", "benefits.read", "benefits.write",
                    "benefits.enroll", "exception.read", "exception.write"})


def _user():
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        return c.execute(users.insert().values(email=f"det-{s}@e.com", normalized_email=f"det-{s}@e.com",
            display_name=f"det {s}", auth_subject=f"det-{s}", status="active").returning(users.c.id)).scalar_one()


def _admin(caps=FULL):
    return Principal(_user(), "a@e.com", "A", caps)


def _person(name=None):
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hh = c.execute(households.insert().values(name=f"HH {s}").returning(households.c.id)).scalar_one()
        p = c.execute(people.insert().values(household_id=hh, full_name=name or f"Emp {s}", active=True)
                      .returning(people.c.id)).scalar_one()
    return p, hh


def _org(admin, *, benefits=True, renewal_month=6):
    o = org.create_organization(admin, name=f"Det Co {uuid.uuid4().hex[:6]}")["organization_id"]
    if renewal_month is not None:
        org.update_organization(o, principal=admin, renewal_month=renewal_month)
    if benefits:
        org.add_service_line(o, "benefits", principal=admin, status="active")
    return o


def _employment(admin, org_id, *, hire_days_ago=60, person=None):
    pid, _ = person or _person()
    hire = TODAY - timedelta(days=hire_days_ago) if hire_days_ago is not None else None
    return be.create_employment(admin, organization_id=org_id, person_id=pid, hire_date=hire), pid


def _doc(person_id):
    s = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        return c.execute(documents.insert().values(person_id=person_id, original_name=f"{s}.pdf",
            stored_name=f"{s}.pdf", storage_path=f"/x/{s}", size_bytes=1, sha256=s).returning(documents.c.id)).scalar_one()


def _scan(admin):
    return det.scan_benefits_exceptions(actor_user_id=admin.user_id, today=TODAY)


def _open(org_id, code=None):
    with engine.connect() as c:
        q = (select(exception_types.c.code, exceptions.c.id, exceptions.c.title, exceptions.c.status,
                    exceptions.c.person_id, exceptions.c.household_id, exceptions.c.dedupe_key,
                    exceptions.c.related_entity_type, exceptions.c.related_entity_id, exceptions.c.description)
             .select_from(exceptions.join(exception_types, exception_types.c.id == exceptions.c.exception_type_id))
             .where(exceptions.c.domain == "benefits", exceptions.c.related_entity_type == "organization",
                    exceptions.c.related_entity_id == org_id, exceptions.c.status.notin_(("resolved", "cancelled"))))
        if code:
            q = q.where(exception_types.c.code == code)
        return [dict(r) for r in c.execute(q).mappings()]


def _codes(org_id):
    return {r["code"] for r in _open(org_id)}


# --- employee / enrollment detectors -----------------------------------------

def test_eligibility_new_hire_and_waiver_detectors_link_organization_and_person():
    a = _admin()
    o = _org(a)
    plan = bd.create_plan(a, organization_id=o, plan_type_code="medical", name="PPO")
    year = bd.create_plan_year(plan["id"], principal=a, plan_year=2027, status="active")
    est_emp, est_p = _employment(a, o, hire_days_ago=90)   # established → eligibility
    new_emp, _ = _employment(a, o, hire_days_ago=5)        # recent → new-hire
    # a third employee marked eligible but never elected/waived, OE window closed
    w_emp, _ = _employment(a, o, hire_days_ago=90)
    w_year = bd.create_plan_year(
        bd.create_plan(a, organization_id=o, plan_type_code="dental", name="Dental")["id"],
        principal=a, plan_year=2027, status="active")
    with engine.begin() as c:  # set the dental year's OE window in the past
        from app.db import benefit_plan_years
        c.execute(benefit_plan_years.update().where(benefit_plan_years.c.id == w_year)
                  .values(open_enrollment_end=TODAY - timedelta(days=10)))
    be.enroll(a, benefit_employment_id=w_emp, plan_year_id=w_year, status="eligible", coverage_tier="employee")
    _scan(a)
    assert "BEN_ELIGIBILITY_UNRESOLVED" in _codes(o)
    assert "BEN_NEW_HIRE_ENROLLMENT_DUE" in _codes(o)
    assert "BEN_WAIVER_MISSING" in _codes(o)
    # organization + employee linkage
    elig = _open(o, "BEN_ELIGIBILITY_UNRESOLVED")
    assert elig[0]["related_entity_id"] == o and elig[0]["person_id"] == est_p
    # auto-resolve: once the established employee enrolls, eligibility clears
    be.enroll(a, benefit_employment_id=est_emp, plan_year_id=year, status="elected", coverage_tier="employee")
    _scan(a)
    assert "BEN_ELIGIBILITY_UNRESOLVED" not in _codes(o)


def test_terminated_still_enrolled_and_effective_date_mismatch():
    a = _admin()
    o = _org(a)
    plan = bd.create_plan(a, organization_id=o, plan_type_code="medical", name="PPO")
    year = bd.create_plan_year(plan["id"], principal=a, plan_year=2027, status="active")
    # terminated employee still showing active coverage (import drift)
    t_emp, _ = _employment(a, o, hire_days_ago=200)
    be.enroll(a, benefit_employment_id=t_emp, plan_year_id=year, status="enrolled")
    with engine.begin() as c:
        c.execute(benefit_employments.update().where(benefit_employments.c.id == t_emp)
                  .values(employee_status="terminated"))
    # coverage effective before hire date
    m_emp, _ = _employment(a, o, hire_days_ago=30)
    be.enroll(a, benefit_employment_id=m_emp, plan_year_id=year, status="elected",
              effective_date=TODAY - timedelta(days=40))
    _scan(a)
    titles = {r["title"] for r in _open(o, "BEN_CENSUS_MISMATCH")}
    assert "Terminated employee still enrolled" in titles
    assert "Enrollment effective-date mismatch" in titles


# --- health / welfare plan detectors -----------------------------------------

def test_health_plan_document_and_renewal_detectors_and_auto_resolve():
    a = _admin()
    o = _org(a)
    plan = bd.create_plan(a, organization_id=o, plan_type_code="medical", name="PPO", status="active")
    with engine.begin() as c:  # renewal within 60 days
        from app.db import benefit_plans
        c.execute(benefit_plans.update().where(benefit_plans.c.id == plan["id"])
                  .values(renewal_date=TODAY + timedelta(days=30)))
    _scan(a)
    assert "BEN_SPD_MISSING" in _codes(o) and "BEN_SBC_MISSING" in _codes(o)
    # renewal-at-risk, plan-year-missing, plan-info-incomplete all raise BEN_RENEWAL_AT_RISK
    # with distinct stable dedupe prefixes
    renewal = _open(o, "BEN_RENEWAL_AT_RISK")
    prefixes = {r["dedupe_key"].rsplit(":", 1)[0] for r in renewal}
    assert {"ben:renewal_risk", "ben:no_plan_year", "ben:plan_info"} <= prefixes
    # auto-resolve: an SPD document arrives
    doc_id = _doc(_person()[0])
    with engine.begin() as c:
        c.execute(benefit_document_links.insert().values(document_id=doc_id, organization_id=o,
                                                         plan_id=plan["id"], doc_kind="spd"))
    _scan(a)
    assert "BEN_SPD_MISSING" not in _codes(o)


# --- retirement-plan detectors + health vs retirement ------------------------

def test_retirement_detectors_and_health_vs_retirement_isolation():
    a = _admin()
    o = _org(a)
    # retirement plan with no plan year + no adoption agreement
    k = bd.create_plan(a, organization_id=o, plan_type_code="401k", name="401k", provider_code="betterment")
    _scan(a)
    codes = _codes(o)
    assert "BEN_PLAN_AMENDMENT_REQUIRED" in codes  # adoption agreement missing
    ret_prefixes = {r["dedupe_key"].rsplit(":", 1)[0] for r in _open(o, "BEN_RENEWAL_AT_RISK")}
    assert "ben:ret_no_plan_year" in ret_prefixes
    # a health-only detector must NOT fire for a retirement-only org
    assert "BEN_ELIGIBILITY_UNRESOLVED" not in codes and "BEN_SPD_MISSING" not in codes
    # add a retirement plan year + an employee with no election → ret-eligibility + deferral-due
    year = bd.create_plan_year(k["id"], principal=a, plan_year=2027, status="active")
    emp, _ = _employment(a, o, hire_days_ago=90)
    _scan(a)
    assert "BEN_RETIREMENT_ELIGIBILITY_UNRESOLVED" in _codes(o)
    enr = be.enroll(a, benefit_employment_id=emp, plan_year_id=year, status="enrolled", coverage_tier="employee")
    _scan(a)
    c2 = _codes(o)
    assert "BEN_RETIREMENT_ELIGIBILITY_UNRESOLVED" not in c2  # cleared by enrolling
    assert "BEN_DEFERRAL_ELECTION_DUE" in c2                  # enrolled but no deferral election
    # setting a deferral election clears deferral-due
    be.set_retirement_election(enr, principal=a, deferral_percent=6, contribution_type="pre_tax")
    _scan(a)
    assert "BEN_DEFERRAL_ELECTION_DUE" not in _codes(o)


# --- employer detectors ------------------------------------------------------

def test_employer_renewal_data_incomplete_and_census_overdue():
    from app.services import engagement_service as es
    a = _admin()
    o = _org(a, renewal_month=None)  # active benefits line but no renewal month
    _scan(a)
    assert any(r["title"] == "Employer renewal data incomplete" for r in _open(o, "BEN_RENEWAL_AT_RISK"))
    # census engagement past due, no census document → census overdue
    es.create_engagement(a, service_line_code="benefits", engagement_type="census_collection",
                         organization_id=o, due_date=TODAY - timedelta(days=3))
    _scan(a)
    assert "BEN_CENSUS_OVERDUE" in _codes(o)


# --- dedupe / no duplicates / reopen -----------------------------------------

def test_stable_dedupe_no_duplicates_and_reopen():
    a = _admin()
    o = _org(a)
    plan = bd.create_plan(a, organization_id=o, plan_type_code="medical", name="PPO")
    year = bd.create_plan_year(plan["id"], principal=a, plan_year=2027, status="active")
    emp, _ = _employment(a, o, hire_days_ago=90)
    _scan(a)
    _scan(a)  # repeated scan must not duplicate the open exception
    elig = _open(o, "BEN_ELIGIBILITY_UNRESOLVED")
    assert len(elig) == 1
    exc_id, key = elig[0]["id"], elig[0]["dedupe_key"]
    # clear (enroll) → auto-resolve
    enr = be.enroll(a, benefit_employment_id=emp, plan_year_id=year, status="elected")
    _scan(a)
    assert _open(o, "BEN_ELIGIBILITY_UNRESOLVED") == []
    # recur (delete the enrollment) → the SAME exception reopens (same dedupe key)
    with engine.begin() as c:
        c.execute(benefit_enrollments.delete().where(benefit_enrollments.c.id == enr))
    _scan(a)
    reopened = _open(o, "BEN_ELIGIBILITY_UNRESOLVED")
    assert len(reopened) == 1 and reopened[0]["id"] == exc_id and reopened[0]["dedupe_key"] == key
    with engine.connect() as c:
        types = [e["event_type"] for e in c.execute(select(exception_events.c.event_type)
                 .where(exception_events.c.exception_id == exc_id)
                 .order_by(exception_events.c.id)).mappings()]
    assert types[0] == "opened" and "resolved" in types and types[-1] == "reopened"


# --- isolation / authorization -----------------------------------------------

def test_cross_organization_isolation_and_authorization():
    owner1 = Principal(_user(), "o1@e.com", "O1", SCOPED)   # scoped, creator → org1 scope
    owner2 = Principal(_user(), "o2@e.com", "O2", SCOPED)
    a = _admin()
    o1 = _org(owner1)
    o2 = _org(owner2)
    for o, owner in ((o1, owner1), (o2, owner2)):
        plan = bd.create_plan(owner, organization_id=o, plan_type_code="medical", name="PPO")
        yr = bd.create_plan_year(plan["id"], principal=owner, plan_year=2027, status="active")
        _employment(owner, o, hire_days_ago=90)
    _scan(a)
    exc1 = _open(o1, "BEN_ELIGIBILITY_UNRESOLVED")[0]
    # owner1 sees org1's benefits exceptions, not org2's
    ids1 = {r["id"] for r in ee.list_exceptions(owner1, domain="benefits")}
    assert exc1["id"] in ids1
    assert all(r["related_entity_id"] != o2 for r in ee.list_exceptions(owner1, domain="benefits")
               if r["related_entity_type"] == "organization")
    # a scoped principal from a different org cannot read org1's exception (hidden)
    with pytest.raises(ee.ExceptionAuthorizationError):
        ee.get_exception(exc1["id"], principal=owner2)


# --- inert disabled-integration types ----------------------------------------

def test_disabled_integration_types_stay_inert():
    a = _admin()
    o = _org(a)
    bd.create_plan(a, organization_id=o, plan_type_code="401k", name="401k", provider_code="betterment")
    _scan(a)
    with engine.connect() as c:
        inert = c.scalar(select(func.count()).select_from(exceptions)
            .select_from(exceptions.join(exception_types, exception_types.c.id == exceptions.c.exception_type_id))
            .where(exceptions.c.domain == "benefits",
                   exception_types.c.code.in_(("BEN_CARRIER_SUBMISSION_FAILED", "BEN_PAYROLL_SYNC_FAILED",
                                               "BEN_PROVIDER_CONNECTION_STALE"))))
    assert inert == 0
    # gaps are documented, not inferred
    assert {"betterment_connection_stale", "payroll_sync_failed", "carrier_submission_failed",
            "qualifying_event_pending"} <= set(det.DETECTOR_GAPS)


# --- no sensitive-data leakage ----------------------------------------------

def test_no_sensitive_data_leakage_in_exception_fields():
    a = _admin()
    o = _org(a)
    secret = f"TopSecretName{uuid.uuid4().hex[:6]}"
    plan = bd.create_plan(a, organization_id=o, plan_type_code="medical", name="PPO")
    bd.create_plan_year(plan["id"], principal=a, plan_year=2027, status="active")
    _employment(a, o, hire_days_ago=90, person=_person(name=secret))
    _scan(a)
    exc = _open(o, "BEN_ELIGIBILITY_UNRESOLVED")[0]
    # title is one of the curated generic titles; the employee name never appears
    assert exc["title"] == "Benefit eligibility unresolved"
    assert secret not in (exc["title"] or "") and secret not in (exc["description"] or "")
    with engine.connect() as c:
        meta_blobs = [str(e["metadata"]) for e in c.execute(select(exception_events.c.metadata)
                      .where(exception_events.c.exception_id == exc["id"])).mappings()]
    assert all(secret not in blob for blob in meta_blobs)


# --- audit + immutable events ------------------------------------------------

def test_audit_and_immutable_events_on_raise():
    a = _admin()
    o = _org(a)
    plan = bd.create_plan(a, organization_id=o, plan_type_code="medical", name="PPO")
    bd.create_plan_year(plan["id"], principal=a, plan_year=2027, status="active")
    _employment(a, o, hire_days_ago=90)
    _scan(a)
    exc = _open(o, "BEN_ELIGIBILITY_UNRESOLVED")[0]
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(exception_events)
                        .where(exception_events.c.exception_id == exc["id"],
                               exception_events.c.event_type == "opened")) == 1
        assert c.scalar(select(func.count()).select_from(audit_events)
                        .where(audit_events.c.action == "exception.raised",
                               audit_events.c.entity_id == str(exc["id"]))) == 1
    # the event ledger is append-only (trigger-enforced)
    with pytest.raises(Exception):
        with engine.begin() as c:
            c.execute(exception_events.update()
                      .where(exception_events.c.exception_id == exc["id"]).values(event_type="tampered"))
