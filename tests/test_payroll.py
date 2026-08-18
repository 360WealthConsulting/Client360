"""Payroll Hub foundation — service + route + access-control tests.

Covers: providers seed, account/employee/run lifecycle, document links (reuse + dedupe), issue lifecycle,
the dashboard summary aggregation, capability enforcement, record-scope isolation, per-business feature
enablement, the module-unavailable guard, and the staff console + client-facing portal gating.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import delete, func, insert, select

from app.db import (
    audit_events,
    client_feature_overrides,
    client_product_entitlements,
    documents,
    engine,
    firm_feature_controls,
    relationship_entities,
)
from app.security.models import Principal
from app.services import payroll as pay
from tests._portal_util import fake_request, seed_staff_user

PAYROLL = frozenset({"payroll.read", "payroll.write", "record.read_all", "record.write_all"})


@pytest.fixture(autouse=True)
def _isolate_features():
    for t in (firm_feature_controls, client_feature_overrides, client_product_entitlements):
        with engine.begin() as c:
            c.execute(delete(t))
    yield


def _staff(caps=PAYROLL):
    return Principal(seed_staff_user(), "s@e.test", "S", frozenset(caps))


def _business(name=None):
    sfx = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        return c.execute(insert(relationship_entities).values(
            entity_type="organization", name=name or f"Biz {sfx}").returning(
            relationship_entities.c.id)).scalar_one()


def _document(name="payroll_report.pdf"):
    sfx = uuid.uuid4().hex
    with engine.begin() as c:
        return c.execute(insert(documents).values(
            original_name=name, stored_name=f"s-{sfx}", storage_provider="Client360 Local",
            storage_path=f"/x/{sfx}", size_bytes=1, sha256=(sfx + sfx)[:64], status="active",
            archived=False).returning(documents.c.id)).scalar_one()


def _audits(action, entity_id):
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(audit_events).where(
            (audit_events.c.action == action) & (audit_events.c.entity_id == str(entity_id))))


# --- providers ---------------------------------------------------------------

def test_providers_are_seeded():
    codes = {p["code"] for p in pay.list_providers()}
    assert {"adp", "quickbooks_payroll", "other"} <= codes


# --- accounts ----------------------------------------------------------------

def test_account_lifecycle_and_audit():
    staff, org = _staff(), _business()
    acct = pay.create_account(staff, organization_id=org, provider_code="adp",
                              external_account_id="CO-123", pay_frequency="biweekly",
                              next_payroll_date=date(2026, 9, 1))
    assert acct["organization_id"] == org and acct["status"] == "active"
    assert acct["external_account_id"] == "CO-123"
    assert _audits("payroll.account.created", acct["id"]) == 1
    got = pay.get_account(acct["id"], principal=staff)
    assert got["id"] == acct["id"]
    assert [a["id"] for a in pay.list_accounts(org, principal=staff)] == [acct["id"]]
    pay.update_account(acct["id"], principal=staff, status="suspended")
    assert pay.get_account(acct["id"], principal=staff)["status"] == "suspended"


def test_account_rejects_bad_enum_and_unknown_provider():
    staff, org = _staff(), _business()
    with pytest.raises(pay.PayrollError):
        pay.create_account(staff, organization_id=org, status="nonsense")
    with pytest.raises(pay.PayrollError):
        pay.create_account(staff, organization_id=org, provider_code="gusto")   # not seeded


# --- employees ---------------------------------------------------------------

def test_employee_roster():
    staff, org = _staff(), _business()
    acct = pay.create_account(staff, organization_id=org)
    e1 = pay.add_employee(staff, payroll_account_id=acct["id"], full_name="Alice Smith",
                          compensation_type="salary", compensation_amount_cents=9_000_000,
                          retirement_plan_participant=True)
    pay.add_employee(staff, payroll_account_id=acct["id"], full_name="Bob Jones",
                     employment_status="terminated")
    active = pay.list_employees(org, principal=staff, status="active")
    assert [e["full_name"] for e in active] == ["Alice Smith"]
    assert active[0]["retirement_plan_participant"] is True
    assert active[0]["compensation_amount_cents"] == 9_000_000
    assert _audits("payroll.employee.added", e1) == 1


def test_employee_requires_name():
    staff, org = _staff(), _business()
    acct = pay.create_account(staff, organization_id=org)
    with pytest.raises(pay.PayrollError):
        pay.add_employee(staff, payroll_account_id=acct["id"], full_name="   ")


# --- runs --------------------------------------------------------------------

def test_run_history_ordered_and_money():
    staff, org = _staff(), _business()
    acct = pay.create_account(staff, organization_id=org)
    pay.record_run(staff, payroll_account_id=acct["id"], pay_date=date(2026, 1, 15),
                   gross_cents=1_000_000, employer_taxes_cents=80_000)
    newest = pay.record_run(staff, payroll_account_id=acct["id"], pay_date=date(2026, 2, 15),
                            gross_cents=1_200_000, employer_taxes_cents=95_000,
                            retirement_contributions_cents=40_000, net_cents=900_000)
    runs = pay.list_runs(org, principal=staff)
    assert [r["id"] for r in runs][0] == newest           # most recent pay_date first
    assert runs[0]["gross_cents"] == 1_200_000 and runs[0]["net_cents"] == 900_000
    pay.set_run_status(newest, "paid", principal=staff)
    assert pay.list_runs(org, principal=staff)[0]["status"] == "paid"


# --- document links (reuse documents; never duplicate) -----------------------

def test_document_link_reuses_document_and_dedupes():
    staff, org = _staff(), _business()
    doc = _document()
    link = pay.link_document(staff, organization_id=org, document_id=doc, category="w2",
                             period_label="2025")
    listed = pay.list_documents(org, principal=staff)
    assert listed[0]["document_id"] == doc and listed[0]["category"] == "w2"
    assert listed[0]["original_name"] == "payroll_report.pdf"
    # The underlying document row is untouched (reuse, not copy).
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(documents).where(documents.c.id == doc)) == 1
    # Same (document, org, category) is rejected.
    with pytest.raises(pay.PayrollError):
        pay.link_document(staff, organization_id=org, document_id=doc, category="w2")
    pay.unlink_document(link, principal=staff)
    assert pay.list_documents(org, principal=staff) == []


def test_document_link_requires_existing_document():
    staff, org = _staff(), _business()
    with pytest.raises(pay.PayrollNotFound):
        pay.link_document(staff, organization_id=org, document_id=99_999_999, category="941")


# --- issues / tasks ----------------------------------------------------------

def test_issue_lifecycle():
    staff, org = _staff(), _business()
    iid = pay.open_issue(staff, organization_id=org, issue_type="missing_filing",
                         title="Q4 941 not filed", severity="high")
    assert [i["id"] for i in pay.list_issues(org, principal=staff, open_only=True)] == [iid]
    pay.set_issue_status(iid, "resolved", principal=staff)
    resolved = pay.list_issues(org, principal=staff, status="resolved")[0]
    assert resolved["resolved_at"] is not None and resolved["resolved_by_user_id"] == staff.user_id
    assert pay.list_issues(org, principal=staff, open_only=True) == []


def test_issue_rejects_bad_type():
    staff, org = _staff(), _business()
    with pytest.raises(pay.PayrollError):
        pay.open_issue(staff, organization_id=org, issue_type="not_a_type", title="x")


# --- dashboard summary -------------------------------------------------------

def test_payroll_summary_cards():
    staff, org = _staff(), _business()
    a1 = pay.create_account(staff, organization_id=org, next_payroll_date=date(2026, 9, 10))
    pay.create_account(staff, organization_id=org, next_payroll_date=date(2026, 9, 3))  # earlier -> min
    pay.add_employee(staff, payroll_account_id=a1["id"], full_name="A", employment_status="active")
    pay.add_employee(staff, payroll_account_id=a1["id"], full_name="B", employment_status="active")
    pay.add_employee(staff, payroll_account_id=a1["id"], full_name="C", employment_status="terminated")
    pay.record_run(staff, payroll_account_id=a1["id"], pay_date=date(2026, 8, 1), gross_cents=500)
    pay.record_run(staff, payroll_account_id=a1["id"], pay_date=date(2026, 8, 20),
                   gross_cents=1_500_000, employer_taxes_cents=120_000, retirement_contributions_cents=50_000)
    pay.open_issue(staff, organization_id=org, issue_type="tax_notice", title="notice")

    s = pay.payroll_summary(org, principal=staff)
    assert s["next_payroll_date"] == date(2026, 9, 3)           # earliest active account
    assert s["employee_count"] == 2                             # active only
    assert s["latest_gross_cents"] == 1_500_000                 # most recent run
    assert s["latest_employer_taxes_cents"] == 120_000
    assert s["latest_retirement_contributions_cents"] == 50_000
    assert s["open_issue_count"] == 1
    assert s["account_count"] == 2


# --- access control: capabilities + record scope -----------------------------

def test_capability_enforcement():
    org = _business()
    reader = _staff({"payroll.read", "record.read_all"})
    with pytest.raises(PermissionError):
        pay.create_account(reader, organization_id=org)        # needs payroll.write
    nobody = _staff({"record.read_all"})
    with pytest.raises(PermissionError):
        pay.list_accounts(org, principal=nobody)               # needs payroll.read


def test_record_scope_isolation():
    org = _business()
    # Full-scope staff can create; a payroll-capable principal WITHOUT firm scope cannot.
    pay.create_account(_staff(), organization_id=org)
    unscoped = Principal(seed_staff_user(), "u@e.test", "U", frozenset({"payroll.read", "payroll.write"}))
    with pytest.raises(PermissionError):
        pay.list_accounts(org, principal=unscoped)
    with pytest.raises(PermissionError):
        pay.create_account(unscoped, organization_id=org)


def test_accounts_are_isolated_per_business():
    staff = _staff()
    org_a, org_b = _business(), _business()
    a = pay.create_account(staff, organization_id=org_a)
    pay.create_account(staff, organization_id=org_b)
    assert [x["id"] for x in pay.list_accounts(org_a, principal=staff)] == [a["id"]]  # only org A's


# --- per-business feature enablement -----------------------------------------

def test_payroll_feature_enablement_per_business():
    from app.services.features import service as feat
    org = _business()
    uid = seed_staff_user()
    assert pay.payroll_enabled_for_org(org) is False           # firm default OFF
    feat.set_firm_state("payroll", "enabled", actor_user_id=uid)
    feat.grant_entitlement("organization", org, "business", actor_user_id=uid)
    assert pay.payroll_enabled_for_org(org) is True
    feat.set_override("organization", org, "payroll", "disable", actor_user_id=uid)
    assert pay.payroll_enabled_for_org(org) is False           # per-client override wins


# --- module-unavailable guard ------------------------------------------------

def test_unavailable_guard(monkeypatch):
    monkeypatch.setattr(pay, "payroll_accounts", None)
    with pytest.raises(pay.PayrollUnavailable):
        pay.list_accounts(1, principal=_staff())


# --- routes: staff console + client-facing portal ----------------------------

def test_staff_dashboard_route_renders():
    from app.routes import payroll as routes
    staff, org = _staff(), _business()
    pay.create_account(staff, organization_id=org)
    resp = routes.payroll_dashboard(org, fake_request(f"/business/{org}/payroll"), principal=staff)
    assert resp.status_code == 200


def test_staff_create_account_route_persists_and_redirects():
    from app.routes import payroll as routes
    staff, org = _staff(), _business()
    resp = routes.create_account(org, provider_code="adp", external_account_id="C1", pay_frequency="monthly",
                                 next_payroll_date="", notes="", principal=staff)
    assert resp.status_code == 303 and resp.headers["location"] == f"/business/{org}/payroll"
    assert len(pay.list_accounts(org, principal=staff)) == 1


def test_portal_route_requires_signed_in_client():
    from fastapi import HTTPException

    from app.routes import payroll as routes
    org = _business()
    with pytest.raises(HTTPException) as ei:
        routes.portal_payroll(org, fake_request(f"/portal/business/{org}/payroll"))
    assert ei.value.status_code == 401                          # no portal principal


def test_portal_route_blocks_out_of_scope_business(monkeypatch):
    from fastapi import HTTPException

    from app.routes import payroll as routes
    org = _business()
    req = fake_request(f"/portal/business/{org}/payroll",
                       state_principal=None)
    # A signed-in portal client whose scope does NOT include this org -> 404 (never reveals payroll).
    import types
    req.state.portal_principal = types.SimpleNamespace(account_id=999)
    monkeypatch.setattr("app.portal.service.portal_scope", lambda account_id, **k: {"organization_ids": []})
    with pytest.raises(HTTPException) as ei:
        routes.portal_payroll(org, req)
    assert ei.value.status_code == 404
