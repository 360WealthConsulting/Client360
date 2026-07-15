"""Release 0.9.11 — Benefits dashboards & reporting (Phase 8) tests.

Reports are built over authorization-filtered reads (record scope applied before aggregation)
from stored data only. Tests use a record-scoped principal (assigned to exactly one fresh
organization) so counts are deterministic on the shared database.
"""
import os
import uuid
from datetime import date, timedelta

import pytest
from starlette.requests import Request

from app.db import engine, households, people, users
from app.security import benefits_crypto
from app.security.models import Principal
from app.routes import benefits as X
from app.services import benefits_domain as bd
from app.services import benefits_enrollment as be
from app.services import benefits_obligations as ob
from app.services import benefits_reporting as br
from app.services import organization_service as org

os.environ.setdefault("BENEFITS_FIELD_KEY", benefits_crypto.generate_key())
TODAY = date.today()

FULL = frozenset({"organization.read", "organization.write", "benefits.read", "benefits.write",
                  "benefits.enroll", "exception.read", "record.read_all", "record.write_all"})
SCOPED = frozenset({"organization.read", "organization.write", "benefits.read", "benefits.write",
                    "benefits.enroll", "exception.read"})  # no record.read_all


def _user():
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        return c.execute(users.insert().values(email=f"r-{s}@e.com", normalized_email=f"r-{s}@e.com",
            display_name=f"R {s}", auth_subject=f"r-{s}", status="active").returning(users.c.id)).scalar_one()


def _p(caps=SCOPED):
    u = _user()
    return Principal(u, f"u{u}@e.com", "R", caps)


def _person():
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hh = c.execute(households.insert().values(name=f"HH {s}").returning(households.c.id)).scalar_one()
        return c.execute(people.insert().values(household_id=hh, full_name=f"Emp {s}", active=True)
                         .returning(people.c.id)).scalar_one()


def _org(principal):
    o = org.create_organization(principal, name=f"Rep Co {uuid.uuid4().hex[:6]}")["organization_id"]
    org.add_service_line(o, "benefits", principal=principal, status="active")
    return o


def _req(path="/benefits/reporting"):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})


# --- book / participation ----------------------------------------------------

def test_book_and_participation_from_stored_data():
    a = _p()                                  # scoped; creator assigned to their org
    o = _org(a)
    plan = bd.create_plan(a, organization_id=o, plan_type_code="medical", name="PPO", status="active")
    k = bd.create_plan(a, organization_id=o, plan_type_code="401k", name="401k",
                       provider_code="betterment", status="active")
    yr = bd.create_plan_year(plan["id"], principal=a, plan_year=2027, status="active")
    p1, p2 = _person(), _person()
    e1 = be.create_employment(a, organization_id=o, person_id=p1)
    be.create_employment(a, organization_id=o, person_id=p2)   # eligible, not enrolled
    be.enroll(a, benefit_employment_id=e1, plan_year_id=yr, status="enrolled")
    rep = br.benefits_report(a)
    assert rep["book"]["employers"] == 1
    assert rep["book"]["plans_by_line"] == {"health": 1, "retirement": 1}
    assert rep["book"]["eligible_employees"] == 2 and rep["book"]["enrolled_employees"] == 1
    assert rep["book"]["participation_rate"] == 0.5


# --- compliance / renewal calendar (stored dates only) -----------------------

def test_compliance_calendar_and_overdue_from_obligations():
    a = _p()
    o = _org(a)
    ob.create_obligation(a, organization_id=o, obligation_type="form_5500",
                         due_date=TODAY + timedelta(days=15), warning_days=60)   # upcoming compliance
    ob.create_obligation(a, organization_id=o, obligation_type="fiduciary_review",
                         due_date=TODAY - timedelta(days=2), warning_days=30)    # overdue compliance
    rep = br.benefits_report(a)
    types = {c["obligation_type"] for c in rep["compliance_calendar"]}
    assert {"form_5500", "fiduciary_review"} <= types
    assert any(x["obligation_type"] == "fiduciary_review" for x in rep["obligations"]["overdue"])
    assert any(x["obligation_type"] == "form_5500" for x in rep["obligations"]["upcoming"])
    # every calendar entry has a real stored date (nothing inferred)
    assert all(c["due_date"] for c in rep["compliance_calendar"])


def test_renewals_pipeline_from_stored_renewal_dates():
    a = _p()
    o = _org(a)
    plan = bd.create_plan(a, organization_id=o, plan_type_code="medical", name="Renewing PPO", status="active")
    with engine.begin() as c:
        from app.db import benefit_plans
        c.execute(benefit_plans.update().where(benefit_plans.c.id == plan["id"])
                  .values(renewal_date=TODAY + timedelta(days=45)))
    rep = br.benefits_report(a)
    assert any(p["name"] == "Renewing PPO" for p in rep["renewals"]["plans_renewing"])


# --- authorization-before-aggregation + scope --------------------------------

def test_authorization_filtering_precedes_aggregation():
    a = _p()
    o = _org(a)
    bd.create_plan(a, organization_id=o, plan_type_code="medical", name="PPO", status="active")
    # a principal with no assignment to this org aggregates none of it
    outsider = _p(SCOPED)
    assert br.benefits_report(outsider)["book"]["employers"] == 0
    # the assigned principal sees it
    assert br.benefits_report(a)["book"]["employers"] >= 1


def test_report_requires_benefits_read_capability():
    from app.services.exception_engine import ExceptionAuthorizationError
    no_cap = Principal(_user(), "n@e.com", "N", frozenset({"organization.read"}))
    with pytest.raises(ExceptionAuthorizationError):
        br.benefits_report(no_cap)


def test_exception_metrics_reused_and_optional():
    a = _p()                                  # has exception.read → exception section present
    assert br.benefits_report(a)["exceptions"] is not None
    no_exc = Principal(_user(), "x@e.com", "X", frozenset({"benefits.read"}))
    assert br.benefits_report(no_exc)["exceptions"] is None


# --- routes / render ---------------------------------------------------------

def test_report_api_and_html_render():
    a = _p(FULL)
    o = _org(a)
    ob.create_obligation(a, organization_id=o, obligation_type="form_5500",
                         due_date=TODAY + timedelta(days=10), warning_days=60)
    api = X.api_benefits_report(principal=a)
    assert "book" in api and "compliance_calendar" in api
    html = X.console_benefits_reporting(_req(), principal=a)
    assert html.status_code == 200 and "text/html" in html.headers["content-type"]
    assert b"Benefits dashboard" in html.body and b"Compliance" in html.body


def test_no_sensitive_data_in_report():
    a = _p()
    o = org.create_organization(a, name="Secret Co", ein="55-1234567")["organization_id"]
    org.add_service_line(o, "benefits", principal=a, status="active")
    secret = f"SecretEmp{uuid.uuid4().hex[:6]}"
    with engine.begin() as c:
        hh = c.execute(households.insert().values(name=f"HH {secret}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(household_id=hh, full_name=secret, active=True).returning(people.c.id)).scalar_one()
    be.create_employment(a, organization_id=o, person_id=pid)
    blob = str(br.benefits_report(a))
    assert "55-1234567" not in blob and secret not in blob  # no EIN, no employee identity
