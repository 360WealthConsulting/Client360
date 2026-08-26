"""Billing & Invoicing service — service agreements, invoices, line items, payments, recurring
schedules, derived balances/status, staff summary, and a read-only Active-client evidence signal.

Authorization split (defense in depth): routes enforce the CAPABILITY (billing.read / billing.write);
this service always enforces RECORD SCOPE on the bill-to subject (person/household via record scope;
organization via organization scope — so ABC LLC billing never leaks under XYZ LLC). Every mutation is
audited. Money is integer USD cents. Balances are derived from settled payments; past-due is derived
from due_date — neither is stored, so nothing can drift.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import func, or_, select

from app.db import (
    billing_schedules,
    engine,
    invoice_line_items,
    invoices,
    payments,
    people,
    service_agreements,
    service_lines,
)
from app.security.audit import write_audit_event
from app.security.authorization import organization_in_scope, record_in_scope
from app.services.billing import constants as k
from app.services.billing.events import emit_billing_event


class BillingError(ValueError):
    """Invalid billing operation (bad state transition, unknown enum, editing an issued invoice)."""


def _now():
    return datetime.now(UTC)


def _today():
    return _now().date()


def _require(table, name):
    if table is None:
        raise BillingError(f"{name} table missing — apply migration billing01 first.")
    return table


def _audit(action, entity_type, entity_id, *, actor_user_id, request_id, metadata):
    write_audit_event(action=action, entity_type=entity_type, entity_id=entity_id,
                      actor_user_id=actor_user_id, request_id=request_id or f"billing-{uuid.uuid4()}",
                      metadata=metadata)


# --- scope --------------------------------------------------------------------

def _validate_subject(bill_to_type):
    if bill_to_type not in k.SUBJECT_TYPES:
        raise BillingError(f"invalid bill_to_type {bill_to_type!r}")


def in_scope(principal, bill_to_type, bill_to_id, *, write=False) -> bool:
    """Record-scope over the bill-to subject: organization → organization scope; else record scope."""
    if bill_to_type == "organization":
        return organization_in_scope(principal, bill_to_id, write=write)
    return record_in_scope(principal, bill_to_type, bill_to_id, write=write)


def _require_scope(principal, bill_to_type, bill_to_id, *, write):
    _validate_subject(bill_to_type)
    if not in_scope(principal, bill_to_type, bill_to_id, write=write):
        raise PermissionError(f"{bill_to_type} is outside your record scope")


# --- loaders ------------------------------------------------------------------

def load_invoice(invoice_id):
    with engine.connect() as c:
        return c.execute(select(invoices).where(invoices.c.id == invoice_id)).mappings().one_or_none()


def load_agreement(agreement_id):
    with engine.connect() as c:
        return c.execute(select(service_agreements).where(
            service_agreements.c.id == agreement_id)).mappings().one_or_none()


def _service_line_id(code):
    if not code:
        return None
    with engine.connect() as c:
        return c.scalar(select(service_lines.c.id).where(service_lines.c.code == code))


# --- service agreements -------------------------------------------------------

def create_agreement(principal, *, bill_to_type, bill_to_id, title, service_line_code=None,
                     default_amount_cents=None, engagement_id=None, tax_engagement_id=None,
                     start_date=None, request_id=None):
    _require(service_agreements, "service_agreements")
    _require_scope(principal, bill_to_type, bill_to_id, write=True)
    if not (title or "").strip():
        raise BillingError("agreement title is required")
    with engine.begin() as c:
        agreement_id = c.execute(service_agreements.insert().values(
            bill_to_type=bill_to_type, bill_to_id=bill_to_id, title=title.strip(),
            service_line_id=_service_line_id(service_line_code), engagement_id=engagement_id,
            tax_engagement_id=tax_engagement_id, default_amount_cents=default_amount_cents,
            start_date=start_date, created_by_user_id=principal.user_id).returning(
            service_agreements.c.id)).scalar_one()
    _audit("billing.agreement.created", "service_agreement", agreement_id, actor_user_id=principal.user_id,
           request_id=request_id, metadata={"bill_to_type": bill_to_type, "bill_to_id": bill_to_id,
                                            "title": title.strip(), "service_line": service_line_code})
    return agreement_id


def set_agreement_status(principal, agreement_id, status, *, request_id=None):
    if status not in k.AGREEMENT_STATUSES:
        raise BillingError(f"invalid agreement status {status!r}")
    ag = load_agreement(agreement_id)
    if ag is None:
        raise BillingError("Agreement not found")
    _require_scope(principal, ag["bill_to_type"], ag["bill_to_id"], write=True)
    with engine.begin() as c:
        c.execute(service_agreements.update().where(service_agreements.c.id == agreement_id).values(
            status=status, updated_at=_now()))
    _audit("billing.agreement.status_changed", "service_agreement", agreement_id,
           actor_user_id=principal.user_id, request_id=request_id,
           metadata={"previous": ag["status"], "new": status})
    return {"agreement_id": agreement_id, "status": status}


def list_agreements(bill_to_type, bill_to_id) -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(select(service_agreements).where(
            service_agreements.c.bill_to_type == bill_to_type,
            service_agreements.c.bill_to_id == bill_to_id).order_by(
            service_agreements.c.created_at.desc())).mappings().all()
    return [dict(r) for r in rows]


def create_schedule(principal, agreement_id, *, frequency, amount_cents, anchor_day=None,
                   next_run_on=None, request_id=None):
    if frequency not in k.FREQUENCIES:
        raise BillingError(f"invalid frequency {frequency!r}")
    ag = load_agreement(agreement_id)
    if ag is None:
        raise BillingError("Agreement not found")
    _require_scope(principal, ag["bill_to_type"], ag["bill_to_id"], write=True)
    with engine.begin() as c:
        schedule_id = c.execute(billing_schedules.insert().values(
            agreement_id=agreement_id, frequency=frequency, amount_cents=int(amount_cents),
            anchor_day=anchor_day, next_run_on=next_run_on or _today()).returning(
            billing_schedules.c.id)).scalar_one()
    _audit("billing.schedule.created", "billing_schedule", schedule_id, actor_user_id=principal.user_id,
           request_id=request_id, metadata={"agreement_id": agreement_id, "frequency": frequency,
                                            "amount_cents": int(amount_cents)})
    return schedule_id


# --- invoices: draft / line items / issue / void ------------------------------

def _new_number():
    return f"INV-{_today():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def create_draft_invoice(principal, *, bill_to_type, bill_to_id, agreement_id=None, due_date=None,
                        schedule_id=None, period_key=None, request_id=None):
    _require(invoices, "invoices")
    _require_scope(principal, bill_to_type, bill_to_id, write=True)
    with engine.begin() as c:
        invoice_id = c.execute(invoices.insert().values(
            bill_to_type=bill_to_type, bill_to_id=bill_to_id, agreement_id=agreement_id,
            schedule_id=schedule_id, period_key=period_key, number=_new_number(), status="draft",
            due_date=due_date, created_by_user_id=principal.user_id).returning(
            invoices.c.id)).scalar_one()
    _audit("billing.invoice.created", "invoice", invoice_id, actor_user_id=principal.user_id,
           request_id=request_id, metadata={"bill_to_type": bill_to_type, "bill_to_id": bill_to_id,
                                            "agreement_id": agreement_id})
    return invoice_id


def _recompute_totals(c, invoice_id):
    rows = c.execute(select(invoice_line_items.c.amount_cents, invoice_line_items.c.kind).where(
        invoice_line_items.c.invoice_id == invoice_id)).mappings().all()
    subtotal = sum(r["amount_cents"] for r in rows if r["kind"] != "credit")
    credit = sum(r["amount_cents"] for r in rows if r["kind"] == "credit")   # stored positive; reduces total
    total = subtotal - credit
    c.execute(invoices.update().where(invoices.c.id == invoice_id).values(
        subtotal_cents=subtotal, credit_cents=credit, total_cents=total, updated_at=_now()))
    return {"subtotal_cents": subtotal, "credit_cents": credit, "total_cents": total}


def _draft_or_raise(invoice_id):
    inv = load_invoice(invoice_id)
    if inv is None:
        raise BillingError("Invoice not found")
    if inv["status"] != "draft":
        raise BillingError("Issued invoice terms are immutable; create an adjustment on a new invoice")
    return inv


def add_line_item(principal, invoice_id, *, description, unit_amount_cents, quantity=1, kind="fee",
                 service_line_code=None, request_id=None):
    if kind not in k.LINE_KINDS:
        raise BillingError(f"invalid line kind {kind!r}")
    inv = _draft_or_raise(invoice_id)
    _require_scope(principal, inv["bill_to_type"], inv["bill_to_id"], write=True)
    amount = int(unit_amount_cents) * int(quantity)
    with engine.begin() as c:
        line_id = c.execute(invoice_line_items.insert().values(
            invoice_id=invoice_id, description=(description or "").strip() or "Item",
            quantity=int(quantity), unit_amount_cents=int(unit_amount_cents), amount_cents=amount,
            kind=kind, service_line_id=_service_line_id(service_line_code)).returning(
            invoice_line_items.c.id)).scalar_one()
        totals = _recompute_totals(c, invoice_id)
    _audit("billing.invoice.line_added", "invoice", invoice_id, actor_user_id=principal.user_id,
           request_id=request_id, metadata={"line_item_id": line_id, "amount_cents": amount, "kind": kind,
                                            "total_cents": totals["total_cents"]})
    return line_id


def remove_line_item(principal, line_item_id, *, request_id=None):
    with engine.connect() as c:
        row = c.execute(select(invoice_line_items.c.invoice_id).where(
            invoice_line_items.c.id == line_item_id)).mappings().one_or_none()
    if row is None:
        raise BillingError("Line item not found")
    inv = _draft_or_raise(row["invoice_id"])
    _require_scope(principal, inv["bill_to_type"], inv["bill_to_id"], write=True)
    with engine.begin() as c:
        c.execute(invoice_line_items.delete().where(invoice_line_items.c.id == line_item_id))
        _recompute_totals(c, row["invoice_id"])
    _audit("billing.invoice.line_removed", "invoice", row["invoice_id"], actor_user_id=principal.user_id,
           request_id=request_id, metadata={"line_item_id": line_item_id})
    return {"invoice_id": row["invoice_id"]}


def issue_invoice(principal, invoice_id, *, due_date=None, request_id=None):
    inv = _draft_or_raise(invoice_id)
    _require_scope(principal, inv["bill_to_type"], inv["bill_to_id"], write=True)
    now = _now()
    with engine.begin() as c:
        totals = _recompute_totals(c, invoice_id)
        c.execute(invoices.update().where(invoices.c.id == invoice_id).values(
            status="issued", issue_date=now.date(), due_date=due_date or inv["due_date"],
            issued_by_user_id=principal.user_id, updated_at=now))
    _audit("billing.invoice.issued", "invoice", invoice_id, actor_user_id=principal.user_id,
           request_id=request_id, metadata={"total_cents": totals["total_cents"],
                                            "bill_to_type": inv["bill_to_type"], "bill_to_id": inv["bill_to_id"]})
    emit_billing_event("invoice.issued", bill_to_type=inv["bill_to_type"], bill_to_id=inv["bill_to_id"],
                       invoice_id=invoice_id, notification_type="invoice_issued",
                       title="New invoice", body=f"Invoice {inv['number']} for "
                       f"{k.money(totals['total_cents'])} is now available.")
    return {"invoice_id": invoice_id, "status": "issued", **totals}


def void_invoice(principal, invoice_id, *, request_id=None):
    inv = load_invoice(invoice_id)
    if inv is None:
        raise BillingError("Invoice not found")
    if inv["status"] == "void":
        return {"invoice_id": invoice_id, "status": "void"}
    _require_scope(principal, inv["bill_to_type"], inv["bill_to_id"], write=True)
    with engine.begin() as c:
        c.execute(invoices.update().where(invoices.c.id == invoice_id).values(
            status="void", voided_by_user_id=principal.user_id, updated_at=_now()))
    _audit("billing.invoice.voided", "invoice", invoice_id, actor_user_id=principal.user_id,
           request_id=request_id, metadata={"previous": inv["status"]})
    emit_billing_event("invoice.voided", bill_to_type=inv["bill_to_type"], bill_to_id=inv["bill_to_id"],
                       invoice_id=invoice_id)
    return {"invoice_id": invoice_id, "status": "void"}


# --- payments + derived balance / status --------------------------------------

def record_payment(principal, invoice_id, *, amount_cents, method="manual", external_ref=None,
                  received_on=None, request_id=None):
    if method not in k.PAYMENT_METHODS:
        raise BillingError(f"invalid payment method {method!r}")
    inv = load_invoice(invoice_id)
    if inv is None:
        raise BillingError("Invoice not found")
    if inv["status"] not in ("issued",):
        raise BillingError("Payments can only be recorded against an issued invoice")
    _require_scope(principal, inv["bill_to_type"], inv["bill_to_id"], write=True)
    with engine.begin() as c:
        payment_id = c.execute(payments.insert().values(
            invoice_id=invoice_id, bill_to_type=inv["bill_to_type"], bill_to_id=inv["bill_to_id"],
            amount_cents=int(amount_cents), method=method, external_ref=external_ref, status="settled",
            received_on=received_on or _today(), recorded_by_user_id=principal.user_id).returning(
            payments.c.id)).scalar_one()
    balance, paid = invoice_balance(invoice_id)
    _audit("billing.payment.recorded", "invoice", invoice_id, actor_user_id=principal.user_id,
           request_id=request_id, metadata={"payment_id": payment_id, "amount_cents": int(amount_cents),
                                            "method": method, "balance_cents": balance})
    emit_billing_event("payment.recorded", bill_to_type=inv["bill_to_type"], bill_to_id=inv["bill_to_id"],
                       invoice_id=invoice_id, notification_type="payment_received",
                       title="Payment received", body=f"We received {k.money(int(amount_cents))} toward "
                       f"invoice {inv['number']}.")
    return {"payment_id": payment_id, "balance_cents": balance, "paid_cents": paid}


def invoice_balance(invoice_id) -> tuple[int, int]:
    """(balance_cents, paid_cents) — derived from SETTLED payments against the invoice total."""
    with engine.connect() as c:
        total = c.scalar(select(invoices.c.total_cents).where(invoices.c.id == invoice_id)) or 0
        paid = c.scalar(select(func.coalesce(func.sum(payments.c.amount_cents), 0)).where(
            payments.c.invoice_id == invoice_id, payments.c.status == "settled")) or 0
    return total - paid, paid


def effective_status(inv: dict, *, as_of=None) -> str:
    """Derive the presented status: past-due and paid/partial are COMPUTED, never stored."""
    as_of = as_of or _today()
    if inv["status"] in ("void", "draft"):
        return inv["status"]
    balance, paid = invoice_balance(inv["id"])
    if balance <= 0:
        return "paid"
    if inv["due_date"] and inv["due_date"] < as_of:
        return "past_due"
    if paid > 0:
        return "partial"
    return "issued"


# --- reads (staff + client share these; access is enforced by the caller) -----

def list_invoices(bill_to_type, bill_to_id, *, include_void=True) -> list[dict]:
    with engine.connect() as c:
        q = select(invoices).where(invoices.c.bill_to_type == bill_to_type,
                                   invoices.c.bill_to_id == bill_to_id)
        if not include_void:
            q = q.where(invoices.c.status != "void")
        rows = c.execute(q.order_by(invoices.c.created_at.desc())).mappings().all()
    out = []
    for r in rows:
        balance, paid = invoice_balance(r["id"])
        out.append({**dict(r), "balance_cents": balance, "paid_cents": paid,
                    "effective_status": effective_status(r)})
    return out


def invoice_detail(invoice_id) -> dict | None:
    inv = load_invoice(invoice_id)
    if inv is None:
        return None
    with engine.connect() as c:
        lines = [dict(x) for x in c.execute(select(invoice_line_items).where(
            invoice_line_items.c.invoice_id == invoice_id).order_by(
            invoice_line_items.c.id)).mappings().all()]
        pays = [dict(x) for x in c.execute(select(payments).where(
            payments.c.invoice_id == invoice_id, payments.c.status == "settled").order_by(
            payments.c.received_on)).mappings().all()]
    balance, paid = invoice_balance(invoice_id)
    return {**dict(inv), "line_items": lines, "payments": pays, "balance_cents": balance,
            "paid_cents": paid, "effective_status": effective_status(inv)}


def payment_history(bill_to_type, bill_to_id) -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(select(payments).where(
            payments.c.bill_to_type == bill_to_type, payments.c.bill_to_id == bill_to_id,
            payments.c.status == "settled").order_by(payments.c.received_on.desc())).mappings().all()
    return [dict(r) for r in rows]


def subject_billing_summary(bill_to_type, bill_to_id) -> dict:
    """Staff at-a-glance: open + overdue balance, last payment, next billing date, counts."""
    invs = list_invoices(bill_to_type, bill_to_id, include_void=False)
    open_balance = sum(i["balance_cents"] for i in invs if i["effective_status"] != "paid")
    overdue_balance = sum(i["balance_cents"] for i in invs if i["effective_status"] == "past_due")
    hist = payment_history(bill_to_type, bill_to_id)
    with engine.connect() as c:
        next_billing = c.scalar(select(func.min(billing_schedules.c.next_run_on)).select_from(
            billing_schedules.join(service_agreements,
                                   service_agreements.c.id == billing_schedules.c.agreement_id)).where(
            service_agreements.c.bill_to_type == bill_to_type,
            service_agreements.c.bill_to_id == bill_to_id, billing_schedules.c.active.is_(True)))
    return {"open_balance_cents": open_balance, "overdue_balance_cents": overdue_balance,
            "last_payment": hist[0] if hist else None, "next_billing_on": next_billing,
            "open_invoice_count": sum(1 for i in invs if i["effective_status"] != "paid")}


# --- manual recurring generation (the automated worker is deferred) -----------

def generate_due_invoices(principal, bill_to_type, bill_to_id, *, as_of=None, request_id=None) -> list[int]:
    """Create draft invoices for the subject's active schedules that are due, idempotently (one invoice
    per schedule+period). Advances each schedule. Manual staff action — no automated worker in the MVP."""
    _require_scope(principal, bill_to_type, bill_to_id, write=True)
    as_of = as_of or _today()
    created = []
    advanced_schedule_ids = []       # schedules whose next_run_on/last_period_key were advanced this run
    with engine.connect() as c:
        sched_rows = c.execute(
            select(billing_schedules, service_agreements.c.title, service_agreements.c.service_line_id).select_from(
                billing_schedules.join(service_agreements,
                                       service_agreements.c.id == billing_schedules.c.agreement_id)).where(
                service_agreements.c.bill_to_type == bill_to_type,
                service_agreements.c.bill_to_id == bill_to_id,
                service_agreements.c.status == "active", billing_schedules.c.active.is_(True),
                billing_schedules.c.next_run_on <= as_of)).mappings().all()
    for s in sched_rows:
        period_key = f"{s['next_run_on']:%Y-%m}"
        if s["last_period_key"] == period_key:
            continue                                             # already generated this period (idempotent)
        invoice_id = create_draft_invoice(
            principal, bill_to_type=bill_to_type, bill_to_id=bill_to_id, agreement_id=s["agreement_id"],
            schedule_id=s["id"], period_key=period_key, request_id=request_id)
        add_line_item(principal, invoice_id, description=s["title"], unit_amount_cents=s["amount_cents"],
                      request_id=request_id)
        with engine.begin() as c:
            c.execute(billing_schedules.update().where(billing_schedules.c.id == s["id"]).values(
                last_period_key=period_key, next_run_on=_advance(s["next_run_on"], s["frequency"]),
                updated_at=_now()))
        created.append(invoice_id)
        advanced_schedule_ids.append(s["id"])
    # Audit names the advanced schedule(s) too: advancing a schedule's next_run_on/last_period_key is a
    # billing-configuration state change, and the per-invoice creates alone don't record which schedules
    # were consumed. schedule_ids makes that transition attributable without a separate event/schema change.
    _audit("billing.invoices.generated", "billing_subject", bill_to_id, actor_user_id=principal.user_id,
           request_id=request_id, metadata={"bill_to_type": bill_to_type, "count": len(created),
                                            "invoice_ids": created, "schedule_ids": advanced_schedule_ids})
    return created


def _advance(run_on: date, frequency: str) -> date:
    if frequency == "monthly":
        y, m = (run_on.year + (run_on.month // 12), (run_on.month % 12) + 1)
        return run_on.replace(year=y, month=m, day=min(run_on.day, 28))
    if frequency == "annual":
        return run_on.replace(year=run_on.year + 1)
    return run_on                                                # one_time / none: no advance


# --- Active-client evidence (READ-ONLY; never mutates client status) ----------

def billing_active_signal(bill_to_type, bill_to_id, *, as_of=None) -> dict:
    """Evidence that a paying/current relationship exists — an INPUT to the status engine only. This
    function never reads or writes client_status; the status engine decides Active."""
    as_of = as_of or _today()
    reasons = []
    with engine.connect() as c:
        has_active_recurring = c.scalar(select(func.count()).select_from(
            billing_schedules.join(service_agreements,
                                   service_agreements.c.id == billing_schedules.c.agreement_id)).where(
            service_agreements.c.bill_to_type == bill_to_type,
            service_agreements.c.bill_to_id == bill_to_id, service_agreements.c.status == "active",
            billing_schedules.c.active.is_(True))) or 0
        recent_payment = c.scalar(select(func.max(payments.c.received_on)).where(
            payments.c.bill_to_type == bill_to_type, payments.c.bill_to_id == bill_to_id,
            payments.c.status == "settled"))
    if has_active_recurring:
        reasons.append("active_recurring_agreement")
    if recent_payment and (as_of - recent_payment).days <= 400:
        reasons.append("recent_settled_payment")
    return {"active_signal": bool(reasons), "reasons": reasons, "as_of": as_of}


# --- client access (portal): subject set + scoped reads -----------------------

def client_billing_subjects(principal) -> set[tuple[str, int]]:
    """The (type, id) subjects a portal client may see billing for — their persons, households, and
    ORGANIZATIONS (per-organization: only orgs the client is associated with, so ABC≠XYZ)."""
    from app.portal.service import portal_scope
    scope = portal_scope(principal.account_id)
    subjects: set[tuple[str, int]] = set()
    subjects.update(("person", pid) for pid in scope["person_ids"])
    subjects.update(("household", hid) for hid in scope["household_ids"])
    subjects.update(("household", hid) for hid in scope["shared_household_ids"])
    subjects.update(("organization", oid) for oid in (scope.get("organization_ids") or set()))
    if principal.person_id is not None:
        with engine.connect() as c:
            hid = c.scalar(select(people.c.household_id).where(people.c.id == principal.person_id))
        subjects.add(("person", principal.person_id))
        if hid is not None:
            subjects.add(("household", hid))
    return subjects


def client_can_access(principal, bill_to_type, bill_to_id) -> bool:
    return (bill_to_type, bill_to_id) in client_billing_subjects(principal)


# --- explicit client projections ---------------------------------------------
# Client billing reads used to serve whole database rows: ``client_invoices`` returned
# ``{**dict(row), ...}`` over the full ``invoices`` row (bill_to ids, agreement/schedule ids,
# period_key, pdf_document_id, the ``metadata`` blob and three staff user ids), and
# ``client_invoice_detail`` returned the STAFF ``invoice_detail`` unchanged — raw line items and raw
# ``payments`` rows including ``external_ref``, ``metadata`` and ``recorded_by_user_id``. Each helper
# below returns a FIXED key set; the staff services are untouched.

def _client_invoice_view(row, *, balance, paid) -> dict:
    return {
        "id": row["id"],                        # required by the invoice detail route
        "number": row["number"],
        "status": row["status"],
        "currency": row["currency"],
        "subtotal_cents": row["subtotal_cents"],
        "credit_cents": row["credit_cents"],
        "total_cents": row["total_cents"],
        "issue_date": row["issue_date"],
        "due_date": row["due_date"],
        "balance_cents": balance,
        "paid_cents": paid,
        "effective_status": effective_status(row),
    }


def _client_line_item_view(row) -> dict:
    return {"description": row["description"], "quantity": row["quantity"],
            "unit_amount_cents": row["unit_amount_cents"], "amount_cents": row["amount_cents"],
            "kind": row["kind"]}


def _client_payment_view(row) -> dict:
    """Never ``external_ref`` (the processor reference), ``metadata`` or ``recorded_by_user_id``."""
    return {"amount_cents": row["amount_cents"], "currency": row["currency"],
            "method": row["method"], "status": row["status"], "received_on": row["received_on"]}


def _client_agreement_view(row) -> dict:
    return {"id": row["id"], "title": row["title"], "status": row["status"],
            "default_amount_cents": row["default_amount_cents"], "currency": row["currency"],
            "start_date": row["start_date"], "end_date": row["end_date"]}


def client_invoices(principal) -> list[dict]:
    # Core feature enforced HERE, not only by the middleware rule on /portal/billing: calling this
    # service directly must not bypass the per-client feature decision.
    from app.services.features.service import client_can
    if not client_can(principal, "billing"):
        return []
    subjects = client_billing_subjects(principal)
    if not subjects:
        return []
    with engine.connect() as c:
        conds = [and_cond for (t, i) in subjects
                 for and_cond in [(invoices.c.bill_to_type == t) & (invoices.c.bill_to_id == i)]]
        rows = c.execute(select(invoices).where(or_(*conds),
                                                invoices.c.status != "draft",   # clients never see drafts
                                                invoices.c.status != "void").order_by(
            invoices.c.created_at.desc()).limit(100)).mappings().all()
    out = []
    for r in rows:
        balance, paid = invoice_balance(r["id"])
        out.append(_client_invoice_view(r, balance=balance, paid=paid))
    return out


def client_invoice_detail(principal, invoice_id) -> dict | None:
    # The more specific middleware rule maps /portal/billing/invoices/{id} to invoice_view; the same
    # feature is enforced here so a direct call cannot bypass it.
    from app.services.features.service import client_can
    if not client_can(principal, "invoice_view"):
        return None
    inv = load_invoice(invoice_id)
    if inv is None or inv["status"] in ("draft", "void"):
        return None                                              # drafts/voids never disclosed to clients
    if not client_can_access(principal, inv["bill_to_type"], inv["bill_to_id"]):
        return None                                             # scope denial hides existence
    staff = invoice_detail(invoice_id)                           # staff structure, never returned as-is
    if staff is None:
        return None
    balance, paid = invoice_balance(invoice_id)
    view = _client_invoice_view(inv, balance=balance, paid=paid)
    view["line_items"] = [_client_line_item_view(l) for l in staff["line_items"]]
    view["payments"] = [_client_payment_view(p) for p in staff["payments"]]
    return view


def client_agreements(principal) -> list[dict]:
    from app.services.features.service import client_can
    if not client_can(principal, "billing"):
        return []
    subjects = client_billing_subjects(principal)
    out = []
    for t, i in subjects:
        out.extend(_client_agreement_view(a) for a in list_agreements(t, i) if a["status"] == "active")
    return out
