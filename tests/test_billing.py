"""Billing & Invoicing MVP — normal lifecycle + comprehensive security/adversarial tests.

Covers: agreement/invoice/line/payment lifecycle, derived balance/status (past-due & paid computed,
never stored), issued-terms immutability, recurring generation (idempotent), staff summary, the
read-only Active-client signal, client-scoped reads, per-organization isolation, cross-client/cross-org
access denial, feature enforcement, audit, forged IDs, and timeline/notification integration.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, insert, select

from app.db import (
    audit_events,
    client_feature_overrides,
    engine,
    firm_feature_controls,
    households,
    people,
    portal_notifications,
)
from app.security.models import Principal
from app.services.billing import service as b
from app.services.features import portal_gate
from app.services.features import service as feat
from tests._portal_util import fake_request, seed_portal_account, seed_staff_user

BILLING = frozenset({"billing.read", "billing.write", "record.read_all", "record.write_all"})


@pytest.fixture(autouse=True)
def _isolate_features():
    for t in (firm_feature_controls, client_feature_overrides):
        with engine.begin() as c:
            c.execute(delete(t))
    yield


def _staff(caps=BILLING):
    return Principal(seed_staff_user(), "s@e.test", "S", frozenset(caps))


def _household():
    sfx = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"HH {sfx}").returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(household_id=hid, full_name=f"P {sfx}", active=True)
                        .returning(people.c.id)).scalar_one()
    return hid, pid


def _audits(action, entity_id):
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(audit_events).where(
            (audit_events.c.action == action) & (audit_events.c.entity_id == str(entity_id))))


def _issued_invoice(staff, hid, *, amount=25000, due=None):
    inv = b.create_draft_invoice(staff, bill_to_type="household", bill_to_id=hid)
    b.add_line_item(staff, inv, description="Service", unit_amount_cents=amount)
    b.issue_invoice(staff, inv, due_date=due)
    return inv


# --- lifecycle + derived math -----------------------------------------------

def test_lifecycle_and_derived_balance_status():
    staff = _staff()
    hid, _ = _household()
    inv = b.create_draft_invoice(staff, bill_to_type="household", bill_to_id=hid)
    b.add_line_item(staff, inv, description="Bookkeeping", unit_amount_cents=25000)
    b.add_line_item(staff, inv, description="Loyalty credit", unit_amount_cents=5000, kind="credit")
    assert b.load_invoice(inv)["total_cents"] == 20000                # 25000 - 5000 credit
    b.issue_invoice(staff, inv, due_date=date.today() + timedelta(days=10))
    assert b.effective_status(b.load_invoice(inv)) == "issued"
    b.record_payment(staff, inv, amount_cents=8000)
    assert b.effective_status(b.load_invoice(inv)) == "partial"      # derived
    assert b.invoice_balance(inv)[0] == 12000                        # derived from settled payments
    b.record_payment(staff, inv, amount_cents=12000)
    assert b.effective_status(b.load_invoice(inv)) == "paid"


def test_past_due_is_derived_not_stored():
    staff = _staff()
    hid, _ = _household()
    inv = _issued_invoice(staff, hid, due=date.today() - timedelta(days=3))
    assert b.load_invoice(inv)["status"] == "issued"                 # stored status stays 'issued'
    assert b.effective_status(b.load_invoice(inv)) == "past_due"     # past-due computed from due_date


def test_issued_terms_are_immutable():
    staff = _staff()
    hid, _ = _household()
    inv = _issued_invoice(staff, hid)
    with pytest.raises(b.BillingError):
        b.add_line_item(staff, inv, description="sneak", unit_amount_cents=1)
    with engine.connect() as c:
        line_id = c.scalar(select(func.min(__import__("app.db", fromlist=["invoice_line_items"])
                                            .invoice_line_items.c.id)))
    if line_id:
        with pytest.raises(b.BillingError):
            b.remove_line_item(staff, line_id)


def test_void_and_payment_state_guards():
    staff = _staff()
    hid, _ = _household()
    draft = b.create_draft_invoice(staff, bill_to_type="household", bill_to_id=hid)
    with pytest.raises(b.BillingError):
        b.record_payment(staff, draft, amount_cents=100)             # cannot pay a draft
    inv = _issued_invoice(staff, hid)
    b.void_invoice(staff, inv)
    assert b.effective_status(b.load_invoice(inv)) == "void"
    with pytest.raises(b.BillingError):
        b.record_payment(staff, inv, amount_cents=100)               # cannot pay a void


# --- recurring generation (idempotent) --------------------------------------

def test_recurring_generation_is_idempotent():
    staff = _staff()
    hid, _ = _household()
    ag = b.create_agreement(staff, bill_to_type="household", bill_to_id=hid, title="Monthly")
    b.create_schedule(staff, ag, frequency="monthly", amount_cents=25000,
                      next_run_on=date.today())
    first = b.generate_due_invoices(staff, "household", hid)
    assert len(first) == 1
    second = b.generate_due_invoices(staff, "household", hid)         # same period → nothing new
    assert second == []


# --- summary + active signal (read-only evidence) ---------------------------

def test_summary_and_active_signal():
    staff = _staff()
    hid, _ = _household()
    assert b.billing_active_signal("household", hid)["active_signal"] is False
    ag = b.create_agreement(staff, bill_to_type="household", bill_to_id=hid, title="Monthly")
    b.create_schedule(staff, ag, frequency="monthly", amount_cents=25000)
    inv = _issued_invoice(staff, hid, due=date.today() - timedelta(days=1))
    b.record_payment(staff, inv, amount_cents=25000)
    sig = b.billing_active_signal("household", hid)
    assert sig["active_signal"] and "active_recurring_agreement" in sig["reasons"]
    assert "recent_settled_payment" in sig["reasons"]
    summary = b.subject_billing_summary("household", hid)
    assert summary["last_payment"]["amount_cents"] == 25000


def test_active_signal_never_mutates_client_status():
    staff = _staff()
    hid, _ = _household()
    feat.set_status("household", hid, "needs_review", actor_user_id=staff.user_id)
    b.create_schedule(staff, b.create_agreement(staff, bill_to_type="household", bill_to_id=hid,
                                                title="M"), frequency="monthly", amount_cents=1000)
    b.billing_active_signal("household", hid)                         # evidence read only
    assert feat.get_status("household", hid)["status"] == "needs_review"   # status untouched


# --- audit -------------------------------------------------------------------

def test_mutations_are_audited():
    staff = _staff()
    hid, _ = _household()
    ag = b.create_agreement(staff, bill_to_type="household", bill_to_id=hid, title="M")
    inv = _issued_invoice(staff, hid)
    b.record_payment(staff, inv, amount_cents=1000)
    assert _audits("billing.agreement.created", ag) >= 1
    assert _audits("billing.invoice.issued", inv) >= 1
    assert _audits("billing.payment.recorded", inv) >= 1


# --- Security: cross-client / scope -----------------------------------------

def test_staff_without_record_scope_is_denied():
    scoped = _staff()
    unscoped = Principal(seed_staff_user(), "u@e.test", "U", frozenset({"billing.write"}))  # no record scope
    hid, _ = _household()
    with pytest.raises(PermissionError):
        b.create_agreement(unscoped, bill_to_type="household", bill_to_id=hid, title="x")
    inv = _issued_invoice(scoped, hid)
    with pytest.raises(PermissionError):
        b.record_payment(unscoped, inv, amount_cents=100)            # out-of-scope payment denied


def test_client_sees_only_own_invoices():
    staff = _staff()
    _, alice, apid, ahid = seed_portal_account(seed_staff_user())
    _, bob, bpid, bhid = seed_portal_account(seed_staff_user())
    a_inv = _issued_invoice(staff, ahid)
    b_inv = _issued_invoice(staff, bhid)
    alice_ids = {i["id"] for i in b.client_invoices(alice)}
    assert a_inv in alice_ids and b_inv not in alice_ids
    assert b.client_invoice_detail(alice, b_inv) is None             # cannot open another client's invoice
    assert b.client_invoice_detail(alice, a_inv) is not None


def test_client_never_sees_draft_or_void():
    staff = _staff()
    _, alice, _, ahid = seed_portal_account(seed_staff_user())
    draft = b.create_draft_invoice(staff, bill_to_type="household", bill_to_id=ahid)
    voided = _issued_invoice(staff, ahid)
    b.void_invoice(staff, voided)
    ids = {i["id"] for i in b.client_invoices(alice)}
    assert draft not in ids and voided not in ids
    assert b.client_invoice_detail(alice, draft) is None


# --- Security: per-organization business isolation --------------------------

def test_business_invoice_isolated_per_organization():
    # Staff scoping via organization assignment (like the communication hub s3 test).
    from app.db import record_assignments
    org_x, org_y = int(uuid.uuid4().int % 1_000_000), int(uuid.uuid4().int % 1_000_000)
    staff_x = Principal(seed_staff_user(), "x@e.test", "X", frozenset({"billing.read", "billing.write"}))
    staff_y = Principal(seed_staff_user(), "y@e.test", "Y", frozenset({"billing.read", "billing.write"}))
    with engine.begin() as c:
        for oid, sid in ((org_x, staff_x.user_id), (org_y, staff_y.user_id)):
            c.execute(insert(record_assignments).values(entity_type="organization", entity_id=oid,
                      user_id=sid, assignment_type="owner", effective_date=date.today()))
    # staff_x may create + read ABC (org_x) billing; staff_y (org_y only) may not.
    ag = b.create_agreement(staff_x, bill_to_type="organization", bill_to_id=org_x, title="ABC Advisory")
    assert ag
    with pytest.raises(PermissionError):
        b.create_agreement(staff_y, bill_to_type="organization", bill_to_id=org_x, title="leak")
    assert b.in_scope(staff_x, "organization", org_x) is True
    assert b.in_scope(staff_y, "organization", org_x) is False


def test_client_business_invoice_scoped_to_owned_org():
    # John is associated with ABC only; XYZ invoices must never appear under his account.

    from app.portal.service import (
        accept_invitation,
        create_portal_session,
        invite_portal_account,
        resolve_portal_session,
    )
    from app.services import organization_service as orgsvc
    staff = Principal(seed_staff_user(), "o@e.test", "O",
                      frozenset(BILLING | {"organization.read", "organization.write"}))
    sfx = uuid.uuid4().hex[:8]
    abc = orgsvc.create_organization(staff, name=f"ABC {sfx}", ein=None)["organization_id"]
    xyz = orgsvc.create_organization(staff, name=f"XYZ {sfx}", ein=None)["organization_id"]
    hid, pid = _household()
    account_id, token = invite_portal_account(person_id=pid, household_id=hid, email=f"j-{sfx}@e.test",
        display_name="John", access_type="employer_admin", invited_by_user_id=staff.user_id,
        permissions={"benefits": True}, organization_id=abc)
    accept_invitation(token, f"s-{sfx}", True)
    john = resolve_portal_session(create_portal_session(account_id, device_fingerprint=f"d-{sfx}"))

    abc_inv = _issued_invoice_org(staff, abc)
    xyz_inv = _issued_invoice_org(staff, xyz)
    ids = {i["id"] for i in b.client_invoices(john)}
    assert abc_inv in ids and xyz_inv not in ids                     # ABC visible, XYZ never leaks
    assert b.client_can_access(john, "organization", abc) is True
    assert b.client_can_access(john, "organization", xyz) is False


def _issued_invoice_org(staff, org_id):
    inv = b.create_draft_invoice(staff, bill_to_type="organization", bill_to_id=org_id)
    b.add_line_item(staff, inv, description="Advisory", unit_amount_cents=50000)
    b.issue_invoice(staff, inv)
    return inv


# --- Security: feature enforcement ------------------------------------------

def test_feature_enforcement_billing_and_invoice_view(portal_master_on, production_identity_provider):
    # The firm-wide portal gates now close the client fork; this test is about the per-client
    # Core feature entitlement, so the surface gates are switched on (see tests/conftest.py).
    _, principal, _, hid = seed_portal_account(seed_staff_user())
    staff = _staff()
    assert portal_gate.evaluate(principal, "/portal/billing", "GET")[0]         # billing enabled by default
    feat.set_override("household", hid, "billing", "disable", actor_user_id=staff.user_id)
    allowed, _r, mapped = portal_gate.evaluate(principal, "/portal/billing", "GET")
    assert not allowed and mapped == "billing"
    # invoice detail maps to invoice_view; disabling it blocks the detail route but not the area
    feat.set_override("household", hid, "billing", "inherit", actor_user_id=staff.user_id)
    feat.set_override("household", hid, "invoice_view", "disable", actor_user_id=staff.user_id)
    assert not portal_gate.evaluate(principal, "/portal/billing/invoices/5", "GET")[0]
    assert portal_gate.evaluate(principal, "/portal/billing", "GET")[0]


def test_online_payments_and_autopay_are_firm_disabled_by_default():
    assert feat.firm_state("online_payments") == "disabled"
    assert feat.firm_state("autopay") == "disabled"


# --- Security: forged IDs fail safely ---------------------------------------

def test_forged_ids_fail_safely():
    staff = _staff()
    assert b.load_invoice(999_000_111) is None
    with pytest.raises(b.BillingError):
        b.issue_invoice(staff, 999_000_111)
    with pytest.raises(b.BillingError):
        b.record_payment(staff, 999_000_222, amount_cents=100)
    with pytest.raises(b.BillingError):
        b.set_agreement_status(staff, 999_000_333, "ended")
    with pytest.raises(b.BillingError):
        b.remove_line_item(staff, 999_000_444)


# --- integration: notification + timeline -----------------------------------

def test_issue_emits_client_notification_in_timeline():
    from app.portal import communication_hub as hub
    account_id, principal, pid, hid = seed_portal_account(seed_staff_user())
    staff = _staff()
    _issued_invoice(staff, hid)
    with engine.connect() as c:
        notes = c.scalar(select(func.count()).select_from(portal_notifications).where(
            portal_notifications.c.portal_account_id == account_id,
            portal_notifications.c.entity_type == "invoice"))
    assert notes >= 1                                                # billing event → client notification
    tl = hub.relationship_timeline(person_ids=[pid], household_ids=[hid], account_ids=[account_id])
    assert any(e["kind"] == "notification" for e in tl)              # surfaced in the shared timeline


# --- staff routes (capability + scope wired) --------------------------------

def test_staff_routes_capability_and_scope():
    from app.routes.billing import billing_panel, create_agreement, record_payment
    from app.security.dependencies import require_capability
    _, _, _, hid = seed_portal_account(seed_staff_user())
    staff = _staff()
    req = SimpleNamespace(state=SimpleNamespace(request_id="t"), query_params={})
    # capability gate: no billing.read
    with pytest.raises(HTTPException) as ei:
        require_capability("billing.read")(principal=Principal(1, "n@e", "N", frozenset()))
    assert ei.value.status_code == 403
    # authorized staff panel renders (staff base template needs a full request)
    panel_req = fake_request(f"/billing/household/{hid}", state_principal=staff)
    html = billing_panel("household", hid, panel_req, principal=staff).body.decode()
    assert "Billing" in html
    # create agreement via route
    resp = create_agreement("household", hid, req, title="Advisory", service_line_code=None,
                            amount="250.00", principal=staff)
    assert resp.status_code == 303
    inv = _issued_invoice(staff, hid)
    # out-of-scope staff blocked at the route
    outsider = Principal(seed_staff_user(), "u@e", "U", frozenset({"billing.write"}))
    with pytest.raises(HTTPException) as ei:
        record_payment(inv, req, amount="10.00", method="manual", external_ref=None, principal=outsider)
    assert ei.value.status_code == 403
