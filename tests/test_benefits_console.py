"""Release 0.9.11 — Organization & Benefits staff API + consoles (Phase 6) tests.

Routes are exercised by calling their functions directly with an explicit Principal (the DI
capability gate is tested separately) and a hand-built Request for HTML renders. The canonical
services still enforce capability and Organization record scope on every call.
"""
import os
import uuid
from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.db import engine, households, people, users
from app.security import benefits_crypto
from app.security.models import Principal
from app.routes import benefits as X

os.environ.setdefault("BENEFITS_FIELD_KEY", benefits_crypto.generate_key())
TODAY = date.today()

FULL = frozenset({"organization.read", "organization.write", "benefits.read", "benefits.write",
                  "benefits.enroll", "benefits.compliance", "benefits.sensitive.read",
                  "exception.read", "record.read_all", "record.write_all"})
SCOPED = frozenset({"organization.read", "organization.write", "benefits.read", "benefits.write",
                    "benefits.enroll"})  # no record.read_all / sensitive


def _user():
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        return c.execute(users.insert().values(email=f"c-{s}@e.com", normalized_email=f"c-{s}@e.com",
            display_name=f"Staff {s}", auth_subject=f"c-{s}", status="active").returning(users.c.id)).scalar_one()


def _p(caps=FULL):
    u = _user()
    return Principal(u, f"u{u}@e.com", f"U{u}", caps)


def _person():
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hh = c.execute(households.insert().values(name=f"HH {s}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(household_id=hh, full_name=f"Employee {s}", active=True)
                        .returning(people.c.id)).scalar_one()
    return pid


def _req(path="/organizations"):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})


def _org(principal, **kw):
    return X.api_org_create(X.OrgCreate(name=kw.pop("name", f"Acme {uuid.uuid4().hex[:6]}"), **kw),
                            principal=principal)["organization_id"]


# --- organization JSON API ---------------------------------------------------

def test_org_crud_and_structure_api():
    a = _p()
    oid = _org(a, industry="Manufacturing", ein="12-3456789")
    assert any(o["id"] == oid for o in X.api_org_list(principal=a)["organizations"])
    got = X.api_org_get(oid, principal=a)
    assert got["organization_id"] == oid and got.get("ein_present")  # ein hidden without include_sensitive
    assert X.api_org_get(oid, include_sensitive=True, principal=a)["ein"] == "12-3456789"
    X.api_org_update(oid, X.OrgUpdate(industry="Aerospace"), principal=a)
    assert X.api_org_get(oid, principal=a)["industry"] == "Aerospace"
    # service lines + roles + ownership + engagements
    X.api_org_add_service_line(oid, X.ServiceLineBody(service_line_code="benefits"), principal=a)
    assert any(sl["code"] == "benefits" for sl in X.api_org_service_lines(oid, principal=a)["service_lines"])
    staff = _user()
    X.api_org_add_role(oid, X.RoleBody(user_id=staff, role_code="renewal_owner", is_primary=True), principal=a)
    assert any(r["user_id"] == staff for r in X.api_org_roles(oid, principal=a)["roles"])
    person = _person()
    X.api_org_ownership(oid, X.OwnershipBody(owner_person_id=person, ownership_percentage=100), principal=a)
    assert X.api_org_owners(oid, principal=a)["owners"]
    X.api_org_create_engagement(oid, X.EngagementBody(service_line_code="benefits",
                                engagement_type="benefit_renewal"), principal=a)
    assert X.api_org_engagements(oid, principal=a)["engagements"]


def test_benefits_plan_enrollment_and_obligation_api():
    a = _p()
    oid = _org(a)
    X.api_org_add_service_line(oid, X.ServiceLineBody(service_line_code="benefits"), principal=a)
    plan = X.api_create_plan(oid, X.PlanBody(plan_type_code="medical", name="PPO"), principal=a)
    assert plan["status"] == "draft"
    assert any(p["id"] == plan["id"] for p in X.api_plans(oid, principal=a)["plans"])
    yr = X.api_create_plan_year(plan["id"], X.PlanYearBody(plan_year=2027, status="active"), principal=a)["id"]
    person = _person()
    emp = X.api_create_employment(oid, X.EmploymentBody(person_id=person), principal=a)["id"]
    X.api_create_enrollment(X.EnrollmentBody(benefit_employment_id=emp, plan_year_id=yr), principal=a)
    # obligations
    created = X.api_create_obligation(oid, X.ObligationBody(obligation_type="form_5500",
                due_date=TODAY + timedelta(days=30), warning_days=60), principal=a)
    assert any(o["id"] == created["id"] for o in X.api_obligations(oid, principal=a)["obligations"])
    done = X.api_complete_obligation(created["id"], principal=a)
    assert done["status"] == "completed"
    assert X.api_providers(principal=a)["providers"]  # includes seeded Betterment
    with pytest.raises(HTTPException) as bad:
        X.api_create_plan(oid, X.PlanBody(plan_type_code="not_a_type", name="X"), principal=a)
    assert bad.value.status_code == 400


# --- HTML consoles (modern shell, names not IDs) -----------------------------

def test_org_list_and_detail_console_render_names():
    a = _p()
    person = _person()
    oid = _org(a, name="Console Co", ein="99-8887777")
    X.api_org_add_service_line(oid, X.ServiceLineBody(service_line_code="benefits"), principal=a)
    staff = _user()
    with engine.connect() as c:
        staff_name = c.scalar(__import__("sqlalchemy").select(users.c.display_name).where(users.c.id == staff))
        person_name = c.scalar(__import__("sqlalchemy").select(people.c.full_name).where(people.c.id == person))
    X.api_org_add_role(oid, X.RoleBody(user_id=staff, role_code="renewal_owner", is_primary=True), principal=a)
    X.api_org_ownership(oid, X.OwnershipBody(owner_person_id=person, ownership_percentage=75), principal=a)
    X.api_create_plan(oid, X.PlanBody(plan_type_code="401k", name="401(k) Plan", provider_code="betterment"), principal=a)
    X.api_create_obligation(oid, X.ObligationBody(obligation_type="fiduciary_review",
                            due_date=TODAY + timedelta(days=20), warning_days=30), principal=a)
    # list
    lst = X.console_org_list(_req(), principal=a)
    assert lst.status_code == 200 and "text/html" in lst.headers["content-type"]
    assert b"Console Co" in lst.body and b"/organizations/" in lst.body
    # detail — resolves names, shows benefits sections, decrypts EIN for a sensitive principal
    det = X.console_org_detail(oid, _req(f"/organizations/{oid}"), principal=a)
    body = det.body.decode()
    assert det.status_code == 200
    assert staff_name in body and person_name in body           # names, not raw IDs
    assert "401(k) Plan" in body and "Fiduciary Review" in body  # benefits sections rendered
    assert "99-8887777" in body                                  # EIN shown for sensitive principal


def test_ein_masked_without_sensitive_capability():
    a = _p()
    oid = _org(a, ein="55-5554444")
    viewer = Principal(_user(), "v@e.com", "V", frozenset({"organization.read", "record.read_all"}))
    body = X.console_org_detail(oid, _req(f"/organizations/{oid}"), principal=viewer).body.decode()
    assert "55-5554444" not in body and "restricted" in body


def test_benefits_employer_list_console():
    a = _p()
    oid = _org(a, name="Benefits Employer Co")
    X.api_org_add_service_line(oid, X.ServiceLineBody(service_line_code="retirement"), principal=a)
    other = _org(a, name="No Benefits Co")  # no benefits/retirement line → excluded
    resp = X.console_benefits_list(_req("/benefits"), principal=a)
    body = resp.body.decode()
    assert resp.status_code == 200 and "Benefits Employer Co" in body
    assert "No Benefits Co" not in body


# --- authorization -----------------------------------------------------------

def test_capability_dependency_rejects_missing_capability():
    dep = X.require_capability("organization.read")
    with pytest.raises(HTTPException) as exc:
        dep(Principal(1, "x@e.com", "X", frozenset()))
    assert exc.value.status_code == 403


def test_out_of_scope_organization_is_hidden():
    owner = _p(SCOPED)                    # scoped; creator gets scope on their org
    oid = _org(owner)
    outsider = _p(SCOPED)                 # different scoped user, no assignment
    with pytest.raises(HTTPException) as g:
        X.api_org_get(oid, principal=outsider)
    assert g.value.status_code == 404     # out-of-scope hides existence
    with pytest.raises(HTTPException) as p:
        X.api_org_update(oid, X.OrgUpdate(industry="Nope"), principal=outsider)
    assert p.value.status_code == 404
    # the owner still sees it
    assert X.api_org_get(oid, principal=owner)["organization_id"] == oid


def test_thin_routes_translate_not_found():
    a = _p()
    with pytest.raises(HTTPException) as exc:
        X.api_org_get(999_999_999, principal=a)
    assert exc.value.status_code in (403, 404)
