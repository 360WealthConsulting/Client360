"""Portal billing must enforce its Core feature at the SERVICE boundary and project every response
(compliance criterion #3, remediation task 2B1).

Billing was authorized only by the middleware rules in ``portal_gate._RULES`` (``/portal/billing`` →
``billing``, ``/portal/billing/invoices/{id}`` → ``invoice_view``). Calling ``client_invoices`` or
``client_invoice_detail`` directly bypassed that decision entirely. ``client_can_access`` looks like
authorization but is only subject membership, resolved from the same base scope.

It also served raw rows: ``client_invoices`` returned ``{**dict(row), ...}`` over the whole ``invoices``
row, and ``client_invoice_detail`` returned the STAFF ``invoice_detail`` unchanged — raw line items and
raw ``payments`` rows carrying ``external_ref`` (the processor reference), ``metadata`` and
``recorded_by_user_id``.

The staff services are deliberately unchanged; ``test_staff_invoice_detail_still_carries_internals``
proves that by asserting the staff structure still holds what the portal must never show.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import delete, update

from app.db import (
    client_feature_overrides,
    engine,
    firm_feature_controls,
    invoices,
    payments,
)
from app.routes import billing as billing_routes
from app.security.models import Principal
from app.services.billing import service as b
from app.services.features import service as feat
from tests._portal_util import seed_portal_account, seed_staff_user

pytestmark = pytest.mark.usefixtures("portal_master_on")

INTERNAL_BILLING_NOTE = "INTERNAL BILLING NOTE MUST NEVER REACH PORTAL"
INTERNAL_PROCESSOR_REF = "PROC-INTERNAL-REF-MUST-NOT-LEAK"

STAFF_CAPS = frozenset({"billing.read", "billing.write", "record.read_all", "record.write_all"})

#: Billing internals that must never reach a client, at any nesting depth.
FORBIDDEN_FIELDS = {
    "metadata", "provider_metadata", "processor_metadata", "processor_id", "gateway_id",
    "transaction_id", "reconciliation_id", "accounting_reference", "idempotency_key",
    "created_by_user_id", "issued_by_user_id", "voided_by_user_id", "recorded_by_user_id",
    "bill_to_id", "bill_to_type", "schedule_id", "period_key", "pdf_document_id", "agreement_id",
    "service_line_id", "engagement_id", "tax_engagement_id", "external_ref", "internal_notes",
    "staff_notes", "raw_payload", "request_payload", "response_payload", "updated_at",
}

INVOICE_KEYS = {"id", "number", "status", "currency", "subtotal_cents", "credit_cents",
                "total_cents", "issue_date", "due_date", "balance_cents", "paid_cents",
                "effective_status"}
DETAIL_KEYS = INVOICE_KEYS | {"line_items", "payments"}
LINE_KEYS = {"description", "quantity", "unit_amount_cents", "amount_cents", "kind"}
PAYMENT_KEYS = {"amount_cents", "currency", "method", "status", "received_on"}
AGREEMENT_KEYS = {"id", "title", "status", "default_amount_cents", "currency", "start_date",
                  "end_date"}


@pytest.fixture(autouse=True)
def _isolate_features():
    for t in (firm_feature_controls, client_feature_overrides):
        with engine.begin() as c:
            c.execute(delete(t))
    yield


def walk(value, path="$"):
    if isinstance(value, dict):
        for k, v in value.items():
            yield f"{path}.{k}", k, v
            yield from walk(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from walk(v, f"{path}[{i}]")


def assert_clean(payload, label):
    hits = [(p, k) for p, k, _v in walk(payload) if k in FORBIDDEN_FIELDS]
    assert hits == [], f"{label} disclosed internal field(s): {hits}"
    markers = [p for p, _k, v in walk(payload) if isinstance(v, str)
               and (INTERNAL_BILLING_NOTE in v or INTERNAL_PROCESSOR_REF in v)]
    assert markers == [], f"{label} leaked an internal marker at {markers}"


def _staff():
    return Principal(seed_staff_user(), "s@e.test", "S", STAFF_CAPS)


def _client():
    """A portal account plus a staff principal that can bill its household."""
    staff = _staff()
    _acct, principal, _pid, hid = seed_portal_account(seed_staff_user())
    return staff, principal, hid


def _issued_invoice_with_internals(staff, hid):
    """An issued invoice carrying staff-only markers in metadata and the payment processor ref."""
    inv = b.create_draft_invoice(staff, bill_to_type="household", bill_to_id=hid)
    b.add_line_item(staff, inv, description="Advisory service", unit_amount_cents=25000)
    b.issue_invoice(staff, inv, due_date=date.today() + timedelta(days=14))
    b.record_payment(staff, inv, amount_cents=5000, external_ref=INTERNAL_PROCESSOR_REF)
    with engine.begin() as c:
        c.execute(update(invoices).where(invoices.c.id == inv)
                  .values(metadata={"internal_note": INTERNAL_BILLING_NOTE}))
        c.execute(update(payments).where(payments.c.invoice_id == inv)
                  .values(metadata={"internal_note": INTERNAL_BILLING_NOTE}))
    return inv


def _feature(staff, hid, feature, state):
    """Per-client feature override — the same lever the staff Access & Features screen uses."""
    feat.set_override("household", hid, feature, state, actor_user_id=staff.user_id)


# --- projection key sets --------------------------------------------------------

def test_invoice_list_projection_keys_are_exact():
    staff, principal, hid = _client()
    _issued_invoice_with_internals(staff, hid)
    rows = b.client_invoices(principal)
    assert rows, "fixture produced no client invoice"
    for r in rows:
        assert set(r) == INVOICE_KEYS, f"invoice projection drifted: {set(r) ^ INVOICE_KEYS}"


def test_invoice_detail_projection_keys_are_exact():
    staff, principal, hid = _client()
    inv = _issued_invoice_with_internals(staff, hid)
    detail = b.client_invoice_detail(principal, inv)
    assert detail is not None
    assert set(detail) == DETAIL_KEYS, f"detail projection drifted: {set(detail) ^ DETAIL_KEYS}"
    assert detail["line_items"] and detail["payments"]
    for item in detail["line_items"]:
        assert set(item) == LINE_KEYS, f"line item drifted: {set(item) ^ LINE_KEYS}"
    for p in detail["payments"]:
        assert set(p) == PAYMENT_KEYS, f"payment drifted: {set(p) ^ PAYMENT_KEYS}"


def test_payment_history_projection_keys_are_exact():
    staff, principal, hid = _client()
    _issued_invoice_with_internals(staff, hid)
    history = billing_routes._client_payment_history(principal)
    assert history, "fixture produced no payment history"
    for p in history:
        assert set(p) == PAYMENT_KEYS, f"payment history drifted: {set(p) ^ PAYMENT_KEYS}"


# --- disclosure -----------------------------------------------------------------

def test_no_billing_surface_discloses_internal_fields():
    staff, principal, hid = _client()
    inv = _issued_invoice_with_internals(staff, hid)
    assert_clean(b.client_invoices(principal), "client_invoices")
    assert_clean(b.client_invoice_detail(principal, inv), "client_invoice_detail")
    assert_clean(b.client_agreements(principal), "client_agreements")
    assert_clean(billing_routes._client_payment_history(principal), "payment history")


def test_staff_invoice_detail_still_carries_internals():
    """Proves the PORTAL boundary changed, not the staff data model."""
    staff, _principal, hid = _client()
    inv = _issued_invoice_with_internals(staff, hid)
    detail = b.invoice_detail(inv)
    assert detail["metadata"]["internal_note"] == INTERNAL_BILLING_NOTE
    assert detail["bill_to_type"] == "household" and detail["bill_to_id"] == hid
    assert any(p["external_ref"] == INTERNAL_PROCESSOR_REF for p in detail["payments"])
    assert any("recorded_by_user_id" in p for p in detail["payments"])


# --- feature authorization at the SERVICE boundary -------------------------------

def test_billing_feature_off_closes_the_list_surfaces_on_direct_service_call():
    """Middleware is bypassed by calling the service directly; the feature must still be enforced."""
    staff, principal, hid = _client()
    _issued_invoice_with_internals(staff, hid)
    assert b.client_invoices(principal), "invoices should be visible with billing enabled"

    _feature(staff, hid, "billing", "disable")
    assert b.client_invoices(principal) == [], "client_invoices ignored the billing feature"
    assert b.client_agreements(principal) == [], "client_agreements ignored the billing feature"
    assert billing_routes._client_payment_history(principal) == [], \
        "payment history ignored the billing feature"

    _feature(staff, hid, "billing", "inherit")
    assert b.client_invoices(principal), "invoices must return once billing is re-enabled"


def test_invoice_view_off_closes_the_detail_surface_on_direct_service_call():
    staff, principal, hid = _client()
    inv = _issued_invoice_with_internals(staff, hid)
    assert b.client_invoice_detail(principal, inv) is not None

    _feature(staff, hid, "invoice_view", "disable")
    assert b.client_invoice_detail(principal, inv) is None, \
        "client_invoice_detail ignored the invoice_view feature"

    _feature(staff, hid, "invoice_view", "inherit")
    assert b.client_invoice_detail(principal, inv) is not None


# --- cross-client and wrong-subject isolation -------------------------------------

def test_client_a_cannot_see_client_b_invoices():
    staff_a, principal_a, hid_a = _client()
    staff_b, principal_b, hid_b = _client()
    inv_a = _issued_invoice_with_internals(staff_a, hid_a)
    inv_b = _issued_invoice_with_internals(staff_b, hid_b)

    a_ids = {r["id"] for r in b.client_invoices(principal_a)}
    b_ids = {r["id"] for r in b.client_invoices(principal_b)}
    assert inv_a in a_ids and inv_b not in a_ids, "client A saw client B's invoice"
    assert inv_b in b_ids and inv_a not in b_ids, "client B saw client A's invoice"

    assert b.client_invoice_detail(principal_a, inv_b) is None, "A read B's invoice detail"
    assert b.client_invoice_detail(principal_b, inv_a) is None, "B read A's invoice detail"


def test_wrong_subject_and_unrelated_organization_fail_closed():
    _staff_a, principal_a, _hid_a = _client()
    assert b.client_can_access(principal_a, "household", 9_000_001) is False
    assert b.client_can_access(principal_a, "organization", 9_000_002) is False
    assert b.client_can_access(principal_a, "person", 9_000_003) is False


def test_unknown_invoice_id_returns_none_without_disclosure():
    _staff, principal, _hid = _client()
    assert b.client_invoice_detail(principal, 9_123_456) is None
