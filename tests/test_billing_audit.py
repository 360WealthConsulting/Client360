"""Phase 1E — Billing mutation auditability.

Every meaningful Billing mutation must be durably auditable through the platform audit ledger
(``write_audit_event``) with consistent actor / entity / request context, and must never leak sensitive
payment-instrument data. These tests prove, per mutation:
  * a create / update / status-transition emits its audit event with actor_user_id, request_id, the
    affected entity, and enough metadata to understand the state transition;
  * denied mutations emit NO false ``success`` audit event;
  * idempotent / repeated operations do not double-audit;
  * payment audit metadata contains no card/bank/token/external-payload data;
  * portal-visible billing changes stay client-scoped.

No second audit system is used — only the platform ``audit_events`` ledger already used firm-wide.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import insert, select

from app.db import audit_events, engine, households, people
from app.security.models import Principal
from app.services.billing import service as b
from tests._portal_util import seed_portal_account, seed_staff_user

BILLING = frozenset({"billing.read", "billing.write", "record.read_all", "record.write_all"})
RID = "rid-audit-1e"


def _staff(caps=BILLING):
    return Principal(seed_staff_user(), "s@e.test", "S", frozenset(caps))


def _household():
    sfx = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"HH {sfx}").returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(household_id=hid, full_name=f"P {sfx}", active=True)
                        .returning(people.c.id)).scalar_one()
    return hid, pid


def _events(action=None, entity_id=None, actor_user_id=None) -> list[dict]:
    conds = []
    if action is not None:
        conds.append(audit_events.c.action == action)
    if entity_id is not None:
        conds.append(audit_events.c.entity_id == str(entity_id))
    if actor_user_id is not None:
        conds.append(audit_events.c.actor_user_id == actor_user_id)
    with engine.connect() as c:
        q = select(audit_events)
        if conds:
            q = q.where(*conds)
        return [dict(r) for r in c.execute(q.order_by(audit_events.c.id)).mappings().all()]


def _issued(staff, hid, *, amount=25000, due=None):
    inv = b.create_draft_invoice(staff, bill_to_type="household", bill_to_id=hid, request_id=RID)
    b.add_line_item(staff, inv, description="Service", unit_amount_cents=amount, request_id=RID)
    b.issue_invoice(staff, inv, due_date=due, request_id=RID)
    return inv


# --- 1. every mutation emits its audit event with actor/request/entity/metadata --------------------

def test_every_billing_mutation_emits_audit_event():
    staff = _staff()
    hid, _ = _household()

    ag = b.create_agreement(staff, bill_to_type="household", bill_to_id=hid, title="Advisory",
                            default_amount_cents=25000, request_id=RID)
    e = _events("billing.agreement.created", ag)[-1]
    assert e["entity_type"] == "service_agreement" and e["actor_user_id"] == staff.user_id
    assert e["request_id"] == RID and e["outcome"] == "success"
    assert e["metadata"]["bill_to_type"] == "household" and e["metadata"]["bill_to_id"] == hid

    b.set_agreement_status(staff, ag, "paused", request_id=RID)
    e = _events("billing.agreement.status_changed", ag)[-1]
    assert e["metadata"] == {"previous": "active", "new": "paused"}
    b.set_agreement_status(staff, ag, "active", request_id=RID)        # reset so schedule/generate work

    sch = b.create_schedule(staff, ag, frequency="monthly", amount_cents=25000,
                            next_run_on=date.today(), request_id=RID)
    e = _events("billing.schedule.created", sch)[-1]
    assert e["entity_type"] == "billing_schedule"
    assert e["metadata"]["agreement_id"] == ag and e["metadata"]["amount_cents"] == 25000

    inv = b.create_draft_invoice(staff, bill_to_type="household", bill_to_id=hid, request_id=RID)
    e = _events("billing.invoice.created", inv)[-1]
    assert e["entity_type"] == "invoice" and e["metadata"]["bill_to_id"] == hid

    line = b.add_line_item(staff, inv, description="Service", unit_amount_cents=25000, request_id=RID)
    e = _events("billing.invoice.line_added", inv)[-1]
    assert e["metadata"]["line_item_id"] == line and e["metadata"]["total_cents"] == 25000
    assert e["metadata"]["kind"] == "fee"

    scratch = b.add_line_item(staff, inv, description="Scratch", unit_amount_cents=100, request_id=RID)
    b.remove_line_item(staff, scratch, request_id=RID)
    e = _events("billing.invoice.line_removed", inv)[-1]
    assert e["metadata"]["line_item_id"] == scratch

    b.issue_invoice(staff, inv, request_id=RID)
    e = _events("billing.invoice.issued", inv)[-1]
    assert e["metadata"]["total_cents"] == 25000 and e["metadata"]["bill_to_id"] == hid

    pay = b.record_payment(staff, inv, amount_cents=10000, method="check", request_id=RID)
    e = _events("billing.payment.recorded", inv)[-1]
    assert e["metadata"]["payment_id"] == pay["payment_id"] and e["metadata"]["method"] == "check"
    assert e["metadata"]["amount_cents"] == 10000 and e["metadata"]["balance_cents"] == 15000

    b.void_invoice(staff, inv, request_id=RID)
    e = _events("billing.invoice.voided", inv)[-1]
    assert e["metadata"]["previous"] == "issued"

    created = b.generate_due_invoices(staff, "household", hid, request_id=RID)
    e = _events("billing.invoices.generated", hid)[-1]
    assert e["metadata"]["count"] == len(created) and e["metadata"]["invoice_ids"] == created
    assert e["metadata"]["schedule_ids"] == [sch]                     # the advanced schedule is named


# --- 2. no sensitive payment-instrument data in audit metadata -------------------------------------

def test_payment_audit_metadata_has_no_sensitive_instrument_data():
    staff = _staff()
    hid, _ = _household()
    inv = _issued(staff, hid)
    secret_ref = "tok_4111111111111111_live_secret"       # card-like / token-like external reference
    b.record_payment(staff, inv, amount_cents=5000, method="card", external_ref=secret_ref, request_id=RID)
    e = _events("billing.payment.recorded", inv)[-1]
    blob = json.dumps(e["metadata"])
    assert secret_ref not in blob and "4111111111111111" not in blob and "tok_" not in blob
    assert "external_ref" not in e["metadata"]            # the external ref is never written to the audit
    # Only the coarse, non-sensitive transition fields are present; method is a category, not an instrument.
    assert set(e["metadata"]) == {"payment_id", "amount_cents", "method", "balance_cents"}
    assert e["metadata"]["method"] == "card"


# --- 3. denied mutations create NO false success audit ---------------------------------------------

def test_denied_mutations_do_not_create_success_audit():
    hid, _ = _household()
    unscoped = Principal(seed_staff_user(), "u@e.test", "U", frozenset({"billing.write"}))  # no record scope
    with pytest.raises(PermissionError):
        b.create_agreement(unscoped, bill_to_type="household", bill_to_id=hid, title="x", request_id=RID)
    assert _events("billing.agreement.created", actor_user_id=unscoped.user_id) == []

    scoped = _staff()
    inv = _issued(scoped, hid)
    with pytest.raises(PermissionError):
        b.record_payment(unscoped, inv, amount_cents=100, request_id=RID)
    assert _events("billing.payment.recorded", actor_user_id=unscoped.user_id) == []


# --- 4. idempotent / repeated operations do not double-audit ---------------------------------------

def test_idempotent_void_audits_exactly_once():
    staff = _staff()
    hid, _ = _household()
    inv = _issued(staff, hid)
    b.void_invoice(staff, inv, request_id=RID)
    b.void_invoice(staff, inv, request_id=RID)            # already void → no-op, must not re-audit
    assert len(_events("billing.invoice.voided", inv)) == 1


def test_idempotent_generation_no_duplicate_audits():
    staff = _staff()
    hid, _ = _household()
    ag = b.create_agreement(staff, bill_to_type="household", bill_to_id=hid, title="M", request_id=RID)
    sch = b.create_schedule(staff, ag, frequency="monthly", amount_cents=25000,
                            next_run_on=date.today(), request_id=RID)
    first = b.generate_due_invoices(staff, "household", hid, request_id=RID)
    assert len(first) == 1
    second = b.generate_due_invoices(staff, "household", hid, request_id=RID)   # same period → nothing new
    assert second == []
    assert len(_events("billing.invoice.created", first[0])) == 1              # no duplicate invoice.created
    gens = _events("billing.invoices.generated", hid)
    assert gens[-2]["metadata"]["schedule_ids"] == [sch]                       # first run advanced it
    assert gens[-1]["metadata"]["count"] == 0 and gens[-1]["metadata"]["invoice_ids"] == []
    assert gens[-1]["metadata"]["schedule_ids"] == []                          # no-op run advanced nothing


# --- 5. actor + request id are threaded from the ROUTE into the audit ------------------------------

def test_route_threads_actor_and_request_id_into_audit():
    from app.routes.billing import create_agreement as route_create_agreement
    staff = _staff()
    hid, _ = _household()
    req = SimpleNamespace(state=SimpleNamespace(request_id="rid-from-route"), query_params={})
    resp = route_create_agreement("household", hid, req, title="RouteAdvisory",
                                  service_line_code=None, amount="250.00", principal=staff)
    assert resp.status_code == 303
    evs = [e for e in _events("billing.agreement.created", actor_user_id=staff.user_id)
           if e["metadata"].get("title") == "RouteAdvisory"]
    assert evs and evs[-1]["request_id"] == "rid-from-route"
    assert evs[-1]["actor_user_id"] == staff.user_id


# --- 6. portal-visible billing changes remain client-scoped ---------------------------------------

def test_portal_visible_issue_is_scoped_to_owner():
    staff = _staff()
    _, alice, _, ahid = seed_portal_account(seed_staff_user())
    _, bob, _, _ = seed_portal_account(seed_staff_user())
    a_inv = _issued(staff, ahid)                          # issuing makes the invoice portal-visible
    assert a_inv in {i["id"] for i in b.client_invoices(alice)}
    assert a_inv not in {i["id"] for i in b.client_invoices(bob)}     # never visible to another client
    assert b.client_invoice_detail(bob, a_inv) is None               # cross-client detail denied
    # A past-due (portal-visible, derived) invoice is still owner-scoped.
    pd = _issued(staff, ahid, due=date.today() - timedelta(days=5))
    assert pd not in {i["id"] for i in b.client_invoices(bob)}
