"""Release 0.9.11 — Employer portal (Phase 7) tests.

The employer portal reuses the existing portal stack (accounts, grants, documents, secure
messages, notifications). Employer accounts are Organization-scoped; the "Action Needed"
surface is a strict allowlist projected to organization-level, PII-free fields. Scans run
globally, so assertions filter to a fresh Organization.
"""
import io
import os
import uuid
from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy import func, select

from app.db import (audit_events, engine, households, people, portal_notifications, users)
from app.security import benefits_crypto
from app.security.models import Principal
from app.portal import service as ps
from app.portal.service import PortalPrincipal
from app.routes import portal as P
from app.services import benefits_detectors as det
from app.services import engagement_service as es
from app.services import exception_engine as ee
from app.services import organization_service as org

os.environ.setdefault("BENEFITS_FIELD_KEY", benefits_crypto.generate_key())
TODAY = date.today()

STAFF = frozenset({"organization.read", "organization.write", "benefits.read", "benefits.write",
                   "benefits.enroll", "exception.read", "exception.write", "record.read_all", "record.write_all"})
ALLOWED = frozenset({"id", "organization_id", "title", "explanation", "status", "resolved",
                     "due_date", "action_label", "action_kind"})
FORBIDDEN = frozenset({"code", "person_id", "household_id", "owner", "owner_user_id",
                       "escalation_level", "notes", "ein", "category", "severity",
                       "related_entity_type", "sla_due_at"})


def _user():
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        return c.execute(users.insert().values(email=f"e-{s}@e.com", normalized_email=f"e-{s}@e.com",
            display_name=f"Staff {s}", auth_subject=f"e-{s}", status="active").returning(users.c.id)).scalar_one()


def _staff():
    u = _user()
    return Principal(u, f"u{u}@e.com", "S", STAFF)


def _person(name=None):
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hh = c.execute(households.insert().values(name=f"HH {s}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(household_id=hh, full_name=name or f"Person {s}", active=True)
                        .returning(people.c.id)).scalar_one()
    return pid, hh


def _org(staff):
    o = org.create_organization(staff, name=f"Employer {uuid.uuid4().hex[:6]}", ein="12-3456789")["organization_id"]
    org.add_service_line(o, "benefits", principal=staff, status="active")
    return o


def _employer_account(staff, organization_id):
    hr_person, hr_hh = _person()
    account_id, _ = ps.invite_portal_account(
        person_id=hr_person, household_id=hr_hh, email=f"hr-{uuid.uuid4().hex[:6]}@e.com",
        display_name="HR Admin", access_type="employer_admin", invited_by_user_id=staff.user_id,
        permissions={"benefits": True, "census": True, "documents": True, "messages": True},
        organization_id=organization_id)
    return PortalPrincipal(account_id, hr_person, "hr@e.com", "HR Admin")


def _raise(org_id, code, *, person_id=None, dedupe=None, sla=None):
    return ee.raise_exception(code=code, principal=None, actor_user_id=None, source="system",
                              related_entity_type="organization", related_entity_id=org_id,
                              person_id=person_id, sla_due_at=sla, dedupe_key=dedupe or f"{code}-{org_id}")


def _req(path="/portal/benefits/action-needed"):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})


# --- scope + allowlist -------------------------------------------------------

def test_employer_scope_and_allowlist_filtering():
    s = _staff()
    o = _org(s)
    emp = _employer_account(s, o)
    assert ps.portal_scope(emp.account_id, permission="benefits")["organization_ids"] == {o}
    _raise(o, "BEN_CENSUS_OVERDUE")                        # employer-visible
    _raise(o, "BEN_5500_FILING_DUE", dedupe=f"5500-{o}")   # internal compliance — excluded
    _raise(o, "BEN_RENEWAL_AT_RISK", dedupe=f"ren-{o}")    # internal renewal — excluded
    items = ps.employer_action_needed(emp)
    titles = {i["title"] for i in items}
    assert "Employee census needed" in titles
    assert all(i["organization_id"] == o for i in items)
    # only the allowlisted code surfaced
    assert len(items) == 1


def test_no_sensitive_data_or_pii_in_employer_projection():
    s = _staff()
    o = _org(s)
    emp = _employer_account(s, o)
    secret = f"SecretEmployee{uuid.uuid4().hex[:6]}"
    pid, _ = _person(name=secret)
    _raise(o, "BEN_ELIGIBILITY_UNRESOLVED", person_id=pid)
    item = ps.employer_action_needed(emp)[0]
    assert set(item) <= ALLOWED and not (set(item) & FORBIDDEN)
    blob = str(item)
    assert secret not in blob and "12-3456789" not in blob  # no employee name, no EIN


def test_completed_items_drop_and_detail_by_id():
    s = _staff()
    o = _org(s)
    emp = _employer_account(s, o)
    exc = _raise(o, "BEN_CENSUS_OVERDUE")
    assert ps.employer_action_detail(emp, exc["id"])["id"] == exc["id"]
    # resolve it → drops from the active employer view
    ee.begin_work(exc["id"], principal=None, actor_user_id=s.user_id)
    ee.resolve(exc["id"], "handled", principal=None, actor_user_id=s.user_id)
    assert ps.employer_action_needed(emp) == []


# --- isolation / out-of-scope ------------------------------------------------

def test_cross_organization_isolation_and_out_of_scope_404():
    s = _staff()
    o1, o2 = _org(s), _org(s)
    emp1 = _employer_account(s, o1)
    exc2 = _raise(o2, "BEN_CENSUS_OVERDUE")
    # emp1 never sees org2's items
    assert all(i["organization_id"] == o1 for i in ps.employer_action_needed(emp1))
    with pytest.raises(ee.ExceptionNotFoundError):
        ps.employer_action_detail(emp1, exc2["id"])
    # API route hides existence with 404
    with pytest.raises(HTTPException) as g:
        P.api_portal_employer_exception(exc2["id"], principal=emp1)
    assert g.value.status_code == 404


def test_individual_client_account_has_no_employer_items():
    s = _staff()
    # an ordinary (non-employer) portal account: person-scoped, no organization grant
    pid, hh = _person()
    account_id, _ = ps.invite_portal_account(person_id=pid, household_id=hh, email=f"c-{uuid.uuid4().hex[:6]}@e.com",
        display_name="Client", access_type="self", invited_by_user_id=s.user_id,
        permissions={"messages": True, "documents": True})
    client = PortalPrincipal(account_id, pid, "c@e.com", "Client")
    assert ps.employer_organization_ids(client) == []
    assert ps.employer_action_needed(client) == []


# --- census upload clears the exception --------------------------------------

def test_census_upload_clears_census_overdue():
    s = _staff()
    o = _org(s)
    emp = _employer_account(s, o)
    # a census-collection engagement past due, no census doc → detector raises census-overdue
    es.create_engagement(s, service_line_code="benefits", engagement_type="census_collection",
                         organization_id=o, due_date=TODAY - timedelta(days=3))
    det.scan_benefits_exceptions(actor_user_id=s.user_id, today=TODAY)
    assert any(i["title"] == "Employee census needed" for i in ps.employer_action_needed(emp))
    # employer uploads the census through the portal → linked to the org as a census document
    doc_id = ps.employer_census_upload(emp, o, original_name="census.csv",
                                       source=io.BytesIO(b"emp,dob\n1,2000-01-01\n"), content_type="text/csv")
    assert doc_id
    assert _audit_count("benefits.employer.census_uploaded", o) == 1
    # next scan clears census-overdue → gone from the employer view
    det.scan_benefits_exceptions(actor_user_id=s.user_id, today=TODAY)
    assert not any(i["title"] == "Employee census needed" for i in ps.employer_action_needed(emp))


def test_census_upload_out_of_scope_is_hidden():
    s = _staff()
    o1, o2 = _org(s), _org(s)
    emp1 = _employer_account(s, o1)
    with pytest.raises(PermissionError):
        ps.employer_census_upload(emp1, o2, original_name="x.csv", source=io.BytesIO(b"x"), content_type="text/csv")
    with pytest.raises(HTTPException):  # route maps to 404 — run via the async helper below
        import asyncio
        class _F:
            filename = "x.csv"; content_type = "text/csv"; file = io.BytesIO(b"x")
            async def close(self): return None
        asyncio.get_event_loop().run_until_complete(
            P.api_portal_census_upload(o2, file=_F(), principal=emp1))


# --- employer notifications (existing provider/outcome architecture) ---------

def test_employer_notification_is_auditable_and_honest():
    s = _staff()
    o = _org(s)
    emp = _employer_account(s, o)
    with engine.connect() as c:
        before = c.scalar(select(func.count()).select_from(portal_notifications)
                          .where(portal_notifications.c.portal_account_id == emp.account_id))
    nid = ps.notify_employer(emp.account_id, title="Census needed",
                             body="Your benefits team needs an updated census.",
                             entity_type="organization", entity_id=o,
                             idempotency_key=f"emp-notify-{o}")
    assert nid
    with engine.connect() as c:
        row = c.execute(select(portal_notifications.c.status, portal_notifications.c.title, portal_notifications.c.body)
                        .where(portal_notifications.c.id == nid)).mappings().one()
        after = c.scalar(select(func.count()).select_from(portal_notifications)
                         .where(portal_notifications.c.portal_account_id == emp.account_id))
    assert after == before + 1
    assert row["status"] in ("delivered", "disabled")           # honest outcome recorded
    assert "12-3456789" not in str(row["title"]) + str(row["body"])  # no sensitive data
    # idempotent — same key does not create a second notification
    assert ps.notify_employer(emp.account_id, title="Census needed", body="dup",
                              idempotency_key=f"emp-notify-{o}") == nid


# --- HTML render -------------------------------------------------------------

def test_employer_action_needed_page_renders():
    s = _staff()
    o = _org(s)
    emp = _employer_account(s, o)
    _raise(o, "BEN_CENSUS_OVERDUE")
    resp = P.portal_employer_action_needed(_req(), principal=emp)
    assert resp.status_code == 200 and "text/html" in resp.headers["content-type"]
    assert b"Benefits" in resp.body and b"Employee census needed" in resp.body


def _audit_count(action, entity_id):
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(audit_events)
                        .where(audit_events.c.action == action, audit_events.c.entity_id == str(entity_id)))
