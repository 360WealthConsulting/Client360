"""Release 0.9.11 — Employer Operations / Benefits canonical services (Phase 2) tests.

Exercises every new canonical service directly with explicit Principals (routes are Phase 6).
Covers organization CRUD, service lines, permanent roles, ownership (percentages / direct /
multiple owners / org-to-org / both-direction nav), engagements, health + retirement plans,
employment/eligibility/enrollment/waiver/termination/QLE, retirement-election validation,
sensitive-field encryption + unauthorized reads, cross-organization isolation, disabled
providers, the benefits exception authorization branch, and audit events.
"""
import os
import uuid

import pytest
from sqlalchemy import func, select, text

from app.db import audit_events, engine, households, people, users
from app.security import benefits_crypto
from app.security.models import Principal
from app.services import benefits_domain as bd
from app.services import benefits_enrollment as be
from app.services import benefits_providers as bp
from app.services import engagement_service as es
from app.services import exception_engine as ee
from app.services import organization_service as org

os.environ.setdefault("BENEFITS_FIELD_KEY", benefits_crypto.generate_key())

FULL = frozenset({"organization.read", "organization.write", "benefits.read", "benefits.write",
                  "benefits.enroll", "benefits.compliance", "benefits.sensitive.read",
                  "exception.read", "exception.write", "record.read_all", "record.write_all"})
SCOPED = frozenset({"organization.read", "organization.write", "benefits.read", "benefits.write",
                    "benefits.enroll", "exception.read", "exception.write"})  # no record.read_all


def _user(label="ben"):
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        return c.execute(users.insert().values(
            email=f"{label}-{s}@example.com", normalized_email=f"{label}-{s}@example.com",
            display_name=f"{label} {s}", auth_subject=f"{label}-{s}", status="active"
        ).returning(users.c.id)).scalar_one()


def _principal(user_id, caps=FULL):
    return Principal(user_id, f"u{user_id}@e.com", f"U{user_id}", caps)


def _person():
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hh = c.execute(households.insert().values(name=f"HH {s}").returning(households.c.id)).scalar_one()
        p = c.execute(people.insert().values(household_id=hh, full_name=f"Emp {s}", active=True)
                      .returning(people.c.id)).scalar_one()
    return p, hh


def _admin():
    return _principal(_user())


def _org(principal, **kw):
    return org.create_organization(principal, name=kw.pop("name", f"Acme {uuid.uuid4().hex[:6]}"), **kw)


def _audit_count(action, entity_id):
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(audit_events)
                        .where(audit_events.c.action == action, audit_events.c.entity_id == str(entity_id)))


# --- organization CRUD + sensitive ein ---------------------------------------

def test_organization_create_read_update_and_list():
    a = _admin()
    o = _org(a, industry="Manufacturing", status="active")
    oid = o["organization_id"]
    assert o["name"].startswith("Acme") and o["industry"] == "Manufacturing"
    assert _audit_count("organization.created", oid) == 1
    got = org.get_organization(oid, principal=a)
    assert got["organization_id"] == oid
    org.update_organization(oid, principal=a, industry="Aerospace", renewal_month=7)
    assert org.get_organization(oid, principal=a)["industry"] == "Aerospace"
    assert _audit_count("organization.updated", oid) == 1
    assert any(r["id"] == oid for r in org.list_organizations(a))


def test_ein_encrypted_and_gated_by_sensitive_capability():
    a = _admin()
    o = _org(a, ein="12-3456789")
    oid = o["organization_id"]
    # ciphertext at rest, never plaintext
    from app.db import organization_profiles
    with engine.connect() as c:
        stored = c.scalar(select(organization_profiles.c.ein)
                          .where(organization_profiles.c.relationship_entity_id == oid))
    assert stored and stored != "12-3456789"
    # sensitive read returns plaintext for a capable principal
    assert org.get_organization(oid, principal=a, include_sensitive=True)["ein"] == "12-3456789"
    # a principal WITHOUT benefits.sensitive.read never sees it, even asking for sensitive
    no_sens = _principal(a.user_id, SCOPED | {"record.read_all"})
    masked = org.get_organization(oid, principal=no_sens, include_sensitive=True)
    assert masked.get("ein") is None and masked.get("ein_present") is True


# --- service lines + permanent roles -----------------------------------------

def test_service_line_lifecycle():
    a = _admin()
    oid = _org(a)["organization_id"]
    org.add_service_line(oid, "benefits", principal=a, status="active")
    org.add_service_line(oid, "retirement", principal=a, status="prospect")
    lines = {r["code"]: r["status"] for r in org.list_service_lines(oid, principal=a)}
    assert lines == {"benefits": "active", "retirement": "prospect"}
    org.set_service_line_status(oid, "retirement", "active", principal=a)
    assert {r["code"]: r["status"] for r in org.list_service_lines(oid, principal=a)}["retirement"] == "active"
    with pytest.raises(org.OrganizationError):
        org.add_service_line(oid, "benefits", principal=a)  # duplicate


def test_permanent_role_lifecycle():
    a = _admin()
    oid = _org(a)["organization_id"]
    staff = _user("advisor")
    role_id = org.assign_role(oid, principal=a, user_id=staff, role_code="benefits_consultant",
                              service_line_code="benefits", is_primary=True)
    assert _audit_count("organization.role.assigned", oid) == 1
    roles = org.list_roles(oid, principal=a)
    assert any(r["role_code"] == "benefits_consultant" and r["user_id"] == staff for r in roles)
    org.end_role(role_id, principal=a)
    assert org.list_roles(oid, principal=a, active_only=True) == []


# --- ownership ---------------------------------------------------------------

def test_ownership_percentages_multiple_owners_and_direct_indirect():
    a = _admin()
    holdings = _org(a, name="Smith Holdings")["organization_id"]
    abc = _org(a, name="ABC Plumbing")["organization_id"]
    john, _ = _person()
    jane, _ = _person()
    # two direct human owners with percentages
    org.record_ownership(principal=a, owned_organization_id=abc, owner_person_id=john,
                         ownership_percentage=60, voting_percentage=60, ownership_type="individual", is_direct=True)
    org.record_ownership(principal=a, owned_organization_id=abc, owner_person_id=jane,
                         ownership_percentage=40, is_direct=True)
    owners = org.list_owners(abc, principal=a)
    pcts = sorted(float(o["ownership_percentage"]) for o in owners if o["ownership_percentage"] is not None)
    assert pcts == [40.0, 60.0]
    assert _audit_count("organization.ownership.recorded", abc) == 2
    # indirect: Smith Holdings owns ABC 100% (Holdings itself owned by John)
    org.record_ownership(principal=a, owned_organization_id=abc, owner_organization_id=holdings,
                         relationship_code="owns", ownership_percentage=100, is_direct=False)
    indirect = [o for o in org.list_owners(abc, principal=a) if o["is_direct"] is False]
    assert indirect and float(indirect[0]["ownership_percentage"]) == 100.0
    # unknown percentage is allowed (NULL)
    unknown_org = _org(a, name="Mystery LLC")["organization_id"]
    org.record_ownership(principal=a, owned_organization_id=unknown_org, owner_person_id=john,
                         ownership_percentage=None)
    assert any(o["ownership_percentage"] is None for o in org.list_owners(unknown_org, principal=a))


def test_one_owner_many_orgs_and_org_structure_both_directions():
    a = _admin()
    john, _ = _person()
    orgs = [_org(a, name=n)["organization_id"] for n in ("Smith Holdings", "ABC Plumbing", "ABC Rentals")]
    for oid in orgs:
        org.record_ownership(principal=a, owned_organization_id=oid, owner_person_id=john, ownership_percentage=100)
    owned = org.list_owned(principal=a, owner_person_id=john)
    assert {o["organization_id"] for o in owned} >= set(orgs)  # one person → many orgs
    # org-to-org parent/subsidiary structure
    org.record_ownership(principal=a, owned_organization_id=orgs[2], owner_organization_id=orgs[0],
                         relationship_code="parent_of")
    assert any(o["organization_id"] == orgs[2] for o in org.list_owned(principal=a, owner_organization_id=orgs[0]))
    assert any(o["owner_entity_id"] == orgs[0] for o in org.list_owners(orgs[2], principal=a))


def test_ownership_rejects_percentage_over_100():
    a = _admin()
    oid = _org(a)["organization_id"]
    p, _ = _person()
    with pytest.raises(org.OrganizationError):
        org.record_ownership(principal=a, owned_organization_id=oid, owner_person_id=p, ownership_percentage=150)


# --- engagements -------------------------------------------------------------

def test_engagement_lifecycle():
    a = _admin()
    oid = _org(a)["organization_id"]
    e = es.create_engagement(a, service_line_code="benefits", engagement_type="benefit_renewal",
                             organization_id=oid, title="2027 Renewal", due_date=None)
    assert e["status"] == "open" and _audit_count("engagement.created", e["id"]) == 1
    es.update_engagement_status(e["id"], "in_progress", principal=a)
    closed = es.update_engagement_status(e["id"], "closed", principal=a)
    assert closed["status"] == "closed" and closed["closed_on"] is not None
    assert any(x["id"] == e["id"] for x in es.list_engagements(a, organization_id=oid))
    with pytest.raises(es.EngagementError):
        es.create_engagement(a, service_line_code="benefits", engagement_type="x")  # no anchor


# --- plans (health + retirement) + plan years --------------------------------

def test_health_and_retirement_plan_operations():
    a = _admin()
    oid = _org(a)["organization_id"]
    medical = bd.create_plan(a, organization_id=oid, plan_type_code="medical", name="PPO",
                             funding_type="fully_insured")
    assert medical["status"] == "draft" and _audit_count("benefit.plan.created", medical["id"]) == 1
    k401 = bd.create_plan(a, organization_id=oid, plan_type_code="401k", name="401(k) Plan",
                          provider_code="betterment")
    bd.set_retirement_details(k401["id"], principal=a, provider_code="betterment",
                              safe_harbor_type="basic_match", match_formula="100% to 3%",
                              auto_enrollment=True, auto_enroll_default_percent=5, fiduciary_role="3(38)")
    got = bd.get_plan(k401["id"], principal=a)
    assert got["retirement_details"]["safe_harbor_type"] == "basic_match"
    assert got["retirement_details"]["fiduciary_role"] == "3(38)"
    # retirement details are rejected on a non-retirement plan
    with pytest.raises(bd.BenefitsError):
        bd.set_retirement_details(medical["id"], principal=a)
    # plan year lifecycle
    year_id = bd.create_plan_year(k401["id"], principal=a, plan_year=2027, status="upcoming")
    bd.set_plan_year_status(year_id, "open_enrollment", principal=a)
    assert bd.list_plan_years(k401["id"], principal=a)[0]["status"] == "open_enrollment"
    with pytest.raises(bd.BenefitsError):
        bd.create_plan_year(k401["id"], principal=a, plan_year=2027)  # duplicate
    bd.update_plan_status(medical["id"], "active", principal=a)
    assert bd.get_plan(medical["id"], principal=a)["status"] == "active"


# --- employment / enrollment / waiver / termination / QLE --------------------

def test_employment_enrollment_waiver_termination_qle():
    a = _admin()
    oid = _org(a)["organization_id"]
    person_id, _ = _person()
    plan = bd.create_plan(a, organization_id=oid, plan_type_code="medical", name="PPO")
    year = bd.create_plan_year(plan["id"], principal=a, plan_year=2027, status="open_enrollment")
    emp = be.create_employment(a, organization_id=oid, person_id=person_id, benefit_class="full_time")
    assert _audit_count("benefit.employment.created", emp) == 1
    enr = be.enroll(a, benefit_employment_id=emp, plan_year_id=year, coverage_tier="family", status="elected")
    assert be.get_enrollment(enr, principal=a)["status"] == "elected"
    # waiver
    other_year = bd.create_plan_year(
        bd.create_plan(a, organization_id=oid, plan_type_code="dental", name="Dental")["id"],
        principal=a, plan_year=2027)
    w_emp = be.create_employment(a, organization_id=oid, person_id=_person()[0])
    w_enr = be.enroll(a, benefit_employment_id=w_emp, plan_year_id=other_year)
    be.waive(w_enr, principal=a)
    assert be.get_enrollment(w_enr, principal=a)["status"] == "waived"
    # QLE mid-year re-election
    from datetime import date
    qle = be.qualifying_life_event(a, benefit_employment_id=emp, plan_year_id=year,
                                   event_type="marriage", coverage_tier="employee_spouse",
                                   effective_date=date(2027, 6, 1))
    assert be.get_enrollment(qle, principal=a)["coverage_tier"] == "employee_spouse"
    assert _audit_count("benefit.qle.recorded", qle) == 1
    # termination ends the employee's active coverage
    be.terminate_employment(emp, principal=a, termination_date=date(2027, 8, 1))
    assert be.get_enrollment(enr, principal=a)["status"] == "terminated"


def test_retirement_election_validation():
    a = _admin()
    oid = _org(a)["organization_id"]
    person_id, _ = _person()
    k401 = bd.create_plan(a, organization_id=oid, plan_type_code="401k", name="401k", provider_code="betterment")
    year = bd.create_plan_year(k401["id"], principal=a, plan_year=2027)
    emp = be.create_employment(a, organization_id=oid, person_id=person_id)
    enr = be.enroll(a, benefit_employment_id=emp, plan_year_id=year, coverage_tier="employee")
    be.set_retirement_election(enr, principal=a, deferral_percent=6, roth_percent=3, contribution_type="mixed")
    assert be.get_enrollment(enr, principal=a)["retirement_election"]["contribution_type"] == "mixed"
    # roth cannot exceed deferral; percentage must be <= 100
    with pytest.raises(be.EnrollmentError):
        be.set_retirement_election(enr, principal=a, deferral_percent=3, roth_percent=5)
    with pytest.raises(be.EnrollmentError):
        be.set_retirement_election(enr, principal=a, deferral_percent=150)
    # retirement election rejected on a non-retirement enrollment
    med_year = bd.create_plan_year(
        bd.create_plan(a, organization_id=oid, plan_type_code="medical", name="PPO")["id"],
        principal=a, plan_year=2027)
    med_enr = be.enroll(a, benefit_employment_id=emp, plan_year_id=med_year)
    with pytest.raises(be.EnrollmentError):
        be.set_retirement_election(med_enr, principal=a, deferral_percent=5)


# --- cross-organization isolation --------------------------------------------

def test_cross_organization_isolation():
    owner_a = _principal(_user("scopeA"), SCOPED)   # no record.read_all
    owner_b = _principal(_user("scopeB"), SCOPED)
    o_a = org.create_organization(owner_a, name="Org A")["organization_id"]  # creator auto-assigned → scope
    # owner_a sees it; owner_b (unassigned) does not
    assert org.get_organization(o_a, principal=owner_a)["organization_id"] == o_a
    with pytest.raises(PermissionError):
        org.get_organization(o_a, principal=owner_b)
    with pytest.raises(PermissionError):
        org.update_organization(o_a, principal=owner_b, industry="Nope")
    # a firm-wide reader sees any org
    assert org.get_organization(o_a, principal=_admin())["organization_id"] == o_a
    # plan scope follows the org
    plan = bd.create_plan(owner_a, organization_id=o_a, plan_type_code="medical", name="PPO")
    with pytest.raises(PermissionError):
        bd.get_plan(plan["id"], principal=owner_b)


# --- disabled providers ------------------------------------------------------

def test_disabled_providers_report_honest_state():
    for ptype, key in (("carrier", "carrier_disabled"), ("recordkeeper", "betterment"),
                       ("payroll", "payroll_disabled"), ("hris", "hris_disabled")):
        result = bp.connection_status(ptype, key, organization_id=1)
        assert result.outcome == "disabled" and result.status == "not_connected"
    assert bp.get_provider("recordkeeper", "betterment").enabled is False
    with pytest.raises(ValueError):
        bp.get_provider("recordkeeper", "nonexistent")


# --- benefits exception authorization branch ---------------------------------

def test_benefits_exception_authorization_branch():
    a = _admin()
    owner = _principal(_user("exc"), SCOPED)
    oid = org.create_organization(owner, name="Exc Org")["organization_id"]  # owner scoped to org
    # raise a benefits exception anchored to the organization (system caller bypasses authz)
    exc = ee.raise_exception(code="BEN_CENSUS_OVERDUE", actor_user_id=a.user_id, principal=None,
                             source="system", related_entity_type="organization", related_entity_id=oid,
                             dedupe_key=f"ben-census-{oid}")
    assert exc["domain"] == "benefits"
    # the org-scoped owner may read it; an unrelated scoped principal may not (hidden as not-found)
    assert ee.get_exception(exc["id"], principal=owner)["id"] == exc["id"]
    outsider = _principal(_user("out"), SCOPED)
    with pytest.raises(ee.ExceptionAuthorizationError):
        ee.get_exception(exc["id"], principal=outsider)
    # it appears in the org-scoped principal's list, not the outsider's
    assert any(r["id"] == exc["id"] for r in ee.list_exceptions(owner, domain="benefits"))
    assert all(r["id"] != exc["id"] for r in ee.list_exceptions(outsider, domain="benefits"))
