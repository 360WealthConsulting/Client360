"""Insurance commissions — expected/received ledger + reconciliation (Phase 5, NON-REGULATED).

Release 0.10.0, Phase 5. The commission ledger and carrier-statement reconciliation
surface for the insurance book. Purely operational/financial:

- **Expected ledger, split-aware.** ``generate_expected`` fans a commission basis across a
  policy's active producers by ``split_percentage`` — one ``insurance_commissions`` row per
  producer (an ``override`` role credits an upline entity). So a split-commission policy
  credits each producer correctly. ``record_expected`` captures a single entry directly.
- **Received + reconciliation.** ``record_received`` posts a payment and recomputes the
  row's status (received / partial / variance). Carrier statements are imported
  (``import_statement``) and their lines reconciled against expected rows
  (``reconcile_line`` / ``reconcile_statement``), which is where variance surfaces.

Record scope follows the policy (org/person/household), exactly like the rest of the
insurance domain; statements are firm-internal (carrier documents). Every mutation writes a
shared audit event.

This module moves and reconciles money. It makes NO suitability, replacement/1035,
licensing, or CE determination, recommends nothing, and blocks no lifecycle — those
regulated determinations remain behind the AD-5 compliance gate. Date/variance exceptions
live in ``insurance_detectors`` (operational, idempotent, through the SHARED Exception
Engine); the revenue rollup lives in ``insurance_reporting``.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.db import (
    engine,
    insurance_commission_statement_lines,
    insurance_commission_statements,
    insurance_commissions,
    insurance_policies,
    insurance_policy_producers,
    relationship_entities,
)
from app.security.audit import write_audit_event
from app.services import insurance as ins

# A received amount within one cent of expected is a clean reconciliation; anything more is
# a variance worth surfacing (under-payment -> partial, over-payment -> variance).
TOLERANCE = Decimal("0.01")


class CommissionError(RuntimeError):
    """Invalid commission input."""


class CommissionNotFound(CommissionError):
    """The requested commission/statement record does not exist."""


def _now():
    return datetime.now(UTC)


def _rid(request_id):
    return request_id or f"insurance-{uuid.uuid4()}"


def _actor(principal, actor_user_id):
    if actor_user_id is not None:
        return actor_user_id
    return getattr(principal, "user_id", None)


def _require(principal, cap):
    if principal is not None and not principal.can(cap):
        raise PermissionError(f"Missing capability {cap}")


def _money(x):
    if x is None:
        return None
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _reconciled_status(expected, received):
    """Ledger status from the expected/received pair. Operational arithmetic only."""
    if received is None:
        return "expected"
    expected = _money(expected) or Decimal("0.00")
    received = _money(received)
    diff = received - expected
    if abs(diff) <= TOLERANCE:
        return "received"
    if received < expected:
        return "partial"
    return "variance"


# --- scope helpers -----------------------------------------------------------

def _policy_or_403(c, policy_id, principal, *, write):
    """Load a policy and enforce record scope, or raise. Reused for every ledger write."""
    policy = ins._load_policy(c, policy_id)  # raises InsuranceNotFound
    if not ins._policy_scope_ok(principal, policy, write=write, connection=c):
        raise PermissionError("Policy is outside your record scope.")
    return policy


# --- expected ledger ---------------------------------------------------------

def record_expected(principal, *, policy_id, producer_entity_type, producer_entity_id,
                    expected_amount, schedule="first_year", producer_role="writing_agent",
                    split_percentage=None, period_label=None, due_date=None, notes=None,
                    actor_user_id=None, request_id=None):
    """Capture a single expected commission entry for one producer on a policy."""
    _require(principal, "insurance.commissions.write")
    with engine.begin() as c:
        policy = _policy_or_403(c, policy_id, principal, write=True)
        cid = c.execute(insurance_commissions.insert().values(
            policy_id=policy_id, organization_id=policy["organization_id"],
            producer_entity_type=producer_entity_type, producer_entity_id=producer_entity_id,
            producer_role=producer_role, split_percentage=split_percentage,
            schedule=schedule, period_label=period_label, due_date=due_date,
            expected_amount=_money(expected_amount) or Decimal("0.00"),
            status="expected", created_by_user_id=_actor(principal, actor_user_id),
        ).returning(insurance_commissions.c.id)).scalar_one()
    write_audit_event(action="insurance.commission.expected_recorded",
                      entity_type="insurance_commission", entity_id=cid,
                      actor_user_id=_actor(principal, actor_user_id), request_id=_rid(request_id),
                      metadata={"policy_id": policy_id, "schedule": schedule})
    return {"id": cid}


def generate_expected(principal, *, policy_id, basis_amount, schedule="first_year",
                      period_label=None, due_date=None, actor_user_id=None, request_id=None):
    """Fan a commission ``basis_amount`` across the policy's ACTIVE producers by split.

    One ledger row per producer with a positive ``split_percentage`` (an ``override`` role
    credits an upline entity). This is the split-crediting the Phase 5 gate calls for. Pure
    arithmetic — no rate lookup or determination.
    """
    _require(principal, "insurance.commissions.write")
    basis = _money(basis_amount) or Decimal("0.00")
    created = []
    with engine.begin() as c:
        policy = _policy_or_403(c, policy_id, principal, write=True)
        producers = c.execute(select(insurance_policy_producers).where(
            insurance_policy_producers.c.policy_id == policy_id,
            insurance_policy_producers.c.inactive_date.is_(None),
        )).mappings().all()
        for p in producers:
            split = p["split_percentage"]
            if split is None or Decimal(split) <= 0:
                continue  # a producer with no split earns nothing on this run
            expected = _money(basis * Decimal(split) / Decimal("100"))
            cid = c.execute(insurance_commissions.insert().values(
                policy_id=policy_id, organization_id=policy["organization_id"],
                producer_entity_type=p["producer_entity_type"],
                producer_entity_id=p["producer_entity_id"],
                producer_role=p["producer_role"], split_percentage=split,
                schedule=schedule, period_label=period_label, due_date=due_date,
                expected_amount=expected, status="expected",
                created_by_user_id=_actor(principal, actor_user_id),
            ).returning(insurance_commissions.c.id)).scalar_one()
            created.append({"id": cid, "producer_entity_type": p["producer_entity_type"],
                            "producer_entity_id": p["producer_entity_id"],
                            "split_percentage": float(split), "expected_amount": float(expected)})
    write_audit_event(action="insurance.commission.generated",
                      entity_type="insurance_policy", entity_id=policy_id,
                      actor_user_id=_actor(principal, actor_user_id), request_id=_rid(request_id),
                      metadata={"policy_id": policy_id, "schedule": schedule,
                                "basis_amount": float(basis), "entries": len(created)})
    return {"policy_id": policy_id, "created": created, "count": len(created)}


def record_received(principal, commission_id, *, received_amount, statement_id=None,
                    actor_user_id=None, request_id=None):
    """Post a received amount against an expected entry and recompute its status."""
    _require(principal, "insurance.commissions.write")
    with engine.begin() as c:
        row = c.execute(select(insurance_commissions).where(
            insurance_commissions.c.id == commission_id)).mappings().one_or_none()
        if row is None:
            raise CommissionNotFound("Commission entry not found.")
        _policy_or_403(c, row["policy_id"], principal, write=True)
        received = _money(received_amount)
        status = _reconciled_status(row["expected_amount"], received)
        c.execute(insurance_commissions.update().where(
            insurance_commissions.c.id == commission_id).values(
            received_amount=received, status=status, statement_id=statement_id,
            updated_at=_now()))
    write_audit_event(action="insurance.commission.received_recorded",
                      entity_type="insurance_commission", entity_id=commission_id,
                      actor_user_id=_actor(principal, actor_user_id), request_id=_rid(request_id),
                      metadata={"status": status, "statement_id": statement_id})
    return {"id": commission_id, "status": status}


def write_off(principal, commission_id, *, actor_user_id=None, request_id=None):
    """Close an expected entry the firm will not collect (terminal). For a carrier that
    claws paid money back, use ``record_adjustment(kind='chargeback')`` instead."""
    _require(principal, "insurance.commissions.write")
    with engine.begin() as c:
        row = c.execute(select(insurance_commissions).where(
            insurance_commissions.c.id == commission_id)).mappings().one_or_none()
        if row is None:
            raise CommissionNotFound("Commission entry not found.")
        _policy_or_403(c, row["policy_id"], principal, write=True)
        c.execute(insurance_commissions.update().where(
            insurance_commissions.c.id == commission_id).values(
            status="written_off", updated_at=_now()))
    write_audit_event(action="insurance.commission.written_off",
                      entity_type="insurance_commission", entity_id=commission_id,
                      actor_user_id=_actor(principal, actor_user_id), request_id=_rid(request_id))
    return {"id": commission_id, "status": "written_off"}


_ADJUSTMENT_KINDS = ("adjustment", "reversal", "chargeback")


def record_adjustment(principal, commission_id, *, amount, kind="adjustment", reason=None,
                      actor_user_id=None, request_id=None):
    """Apply a signed adjustment to an entry's NET received amount and recompute status.

    One primitive for three back-office corrections, distinguished by ``kind`` in the audit
    trail: ``adjustment`` (a true-up, ±), ``reversal`` and ``chargeback`` (a carrier claws
    money back — normally negative). ``amount`` is a signed delta added to the entry's current
    ``received_amount``; that column stays the single canonical net actual, and the revenue
    rollup re-derives every actual/variance total from it — so totals cannot drift and a
    correction flows straight through. Each call is an immutable audit event; there is no
    second history table.
    """
    _require(principal, "insurance.commissions.write")
    if kind not in _ADJUSTMENT_KINDS:
        raise CommissionError(f"kind must be one of {_ADJUSTMENT_KINDS}.")
    delta = Decimal(str(amount))
    with engine.begin() as c:
        row = c.execute(select(insurance_commissions).where(
            insurance_commissions.c.id == commission_id)).mappings().one_or_none()
        if row is None:
            raise CommissionNotFound("Commission entry not found.")
        _policy_or_403(c, row["policy_id"], principal, write=True)
        base = _money(row["received_amount"]) or Decimal("0.00")
        new_received = _money(base + delta)
        status = _reconciled_status(row["expected_amount"], new_received)
        c.execute(insurance_commissions.update().where(
            insurance_commissions.c.id == commission_id).values(
            received_amount=new_received, status=status, updated_at=_now()))
    write_audit_event(action="insurance.commission.adjusted",
                      entity_type="insurance_commission", entity_id=commission_id,
                      actor_user_id=_actor(principal, actor_user_id), request_id=_rid(request_id),
                      metadata={"kind": kind, "amount": float(delta), "reason": reason, "status": status})
    return {"id": commission_id, "status": status, "received_amount": float(new_received)}


def _decorate(c, row):
    d = dict(row)
    d["producer_name"] = ins._entity_name(c, row["producer_entity_type"], row["producer_entity_id"])
    exp = _money(row["expected_amount"]) or Decimal("0.00")
    rec = _money(row["received_amount"])
    d["variance"] = float(rec - exp) if rec is not None else None
    return d


def get_commission(principal, commission_id):
    _require(principal, "insurance.commissions.read")
    with engine.connect() as c:
        row = c.execute(select(insurance_commissions).where(
            insurance_commissions.c.id == commission_id)).mappings().one_or_none()
        if row is None:
            raise CommissionNotFound("Commission entry not found.")
        policy = ins._load_policy(c, row["policy_id"])
        if not ins._policy_scope_ok(principal, policy, write=False, connection=c):
            raise CommissionNotFound("Commission entry not found.")  # hide existence
        return _decorate(c, row)


def list_commissions(principal, *, policy_id=None, status=None, schedule=None, limit=500):
    """The commission ledger, filtered to the principal's record scope (by policy).

    ``limit=None`` returns the full scoped ledger with no cap — used by the revenue rollup so
    reported totals are a faithful projection of every transaction and never silently truncate.
    """
    _require(principal, "insurance.commissions.read")
    query = select(insurance_commissions).order_by(insurance_commissions.c.id.desc())
    if policy_id:
        query = query.where(insurance_commissions.c.policy_id == policy_id)
    if status:
        query = query.where(insurance_commissions.c.status == status)
    if schedule:
        query = query.where(insurance_commissions.c.schedule == schedule)
    if limit is not None:
        query = query.limit(limit)
    with engine.connect() as c:
        rows = c.execute(query).mappings().all()
        # Resolve each distinct policy's scope once, then filter.
        scope_ok = {}
        for pid in {r["policy_id"] for r in rows}:
            policy = c.execute(select(insurance_policies).where(
                insurance_policies.c.id == pid)).mappings().one_or_none()
            scope_ok[pid] = bool(policy) and ins._policy_scope_ok(principal, policy, write=False, connection=c)
        return [_decorate(c, r) for r in rows if scope_ok.get(r["policy_id"])]


# --- carrier statements + reconciliation -------------------------------------

def import_statement(principal, *, carrier_id, statement_date=None, reference=None,
                     stated_total=None, source="manual", lines=None,
                     actor_user_id=None, request_id=None):
    """Import a carrier commission statement (header + lines). Lines start ``unmatched``;
    call ``reconcile_statement`` to match them to expected ledger rows. Statements are
    firm-internal carrier documents (capability-gated, not record-scoped)."""
    _require(principal, "insurance.commissions.write")
    lines = lines or []
    with engine.begin() as c:
        if c.execute(select(relationship_entities.c.entity_type).where(
                relationship_entities.c.id == carrier_id)).scalar_one_or_none() != "insurance_carrier":
            raise CommissionError("carrier_id must reference an insurance_carrier organization.")
        sid = c.execute(insurance_commission_statements.insert().values(
            carrier_id=carrier_id, statement_date=statement_date, reference=reference,
            stated_total=_money(stated_total), status="imported", source=source,
            created_by_user_id=_actor(principal, actor_user_id),
        ).returning(insurance_commission_statements.c.id)).scalar_one()
        for line in lines:
            c.execute(insurance_commission_statement_lines.insert().values(
                statement_id=sid, policy_number=line.get("policy_number"),
                policy_id=line.get("policy_id"), producer_reference=line.get("producer_reference"),
                schedule=line.get("schedule"), amount=_money(line.get("amount")) or Decimal("0.00"),
                status="unmatched", notes=line.get("notes"),
            ))
    write_audit_event(action="insurance.commission.statement_imported",
                      entity_type="insurance_commission_statement", entity_id=sid,
                      actor_user_id=_actor(principal, actor_user_id), request_id=_rid(request_id),
                      metadata={"carrier_id": carrier_id, "lines": len(lines)})
    return {"id": sid, "lines": len(lines)}


def _match_line_to_commission(c, line):
    """Best expected ledger row for a statement line: same policy (by id or number) and
    schedule where given, still awaiting payment. Deterministic (oldest expected first)."""
    policy_id = line["policy_id"]
    if policy_id is None and line["policy_number"]:
        # policy_number is not schema-unique; pick deterministically (oldest) rather than
        # crash on the rare duplicate — staff can always reconcile the line manually.
        policy_id = c.execute(select(insurance_policies.c.id).where(
            insurance_policies.c.policy_number == line["policy_number"])
            .order_by(insurance_policies.c.id).limit(1)).scalars().first()
    if policy_id is None:
        return None, None
    q = select(insurance_commissions).where(
        insurance_commissions.c.policy_id == policy_id,
        insurance_commissions.c.status.in_(("expected", "partial")))
    if line["schedule"]:
        q = q.where(insurance_commissions.c.schedule == line["schedule"])
    match = c.execute(q.order_by(insurance_commissions.c.id)).mappings().first()
    return policy_id, match


def reconcile_line(principal, line_id, *, commission_id=None, actor_user_id=None, request_id=None):
    """Reconcile a single statement line: link it to an expected ledger row (given or
    auto-matched), post the line amount as received, and recompute the ledger status."""
    _require(principal, "insurance.commissions.write")
    with engine.begin() as c:
        line = c.execute(select(insurance_commission_statement_lines).where(
            insurance_commission_statement_lines.c.id == line_id)).mappings().one_or_none()
        if line is None:
            raise CommissionNotFound("Statement line not found.")
        if commission_id is None:
            _pid, match = _match_line_to_commission(c, line)
            commission_id = match["id"] if match else None
        if commission_id is None:
            return {"line_id": line_id, "matched": False}
        target = c.execute(select(insurance_commissions).where(
            insurance_commissions.c.id == commission_id)).mappings().one_or_none()
        if target is None:
            raise CommissionNotFound("Commission entry not found.")
        _policy_or_403(c, target["policy_id"], principal, write=True)
        received = _money(line["amount"])
        status = _reconciled_status(target["expected_amount"], received)
        c.execute(insurance_commissions.update().where(
            insurance_commissions.c.id == commission_id).values(
            received_amount=received, status=status,
            statement_id=line["statement_id"], updated_at=_now()))
        c.execute(insurance_commission_statement_lines.update().where(
            insurance_commission_statement_lines.c.id == line_id).values(
            matched_commission_id=commission_id, policy_id=target["policy_id"],
            status="reconciled", updated_at=_now()))
    write_audit_event(action="insurance.commission.line_reconciled",
                      entity_type="insurance_commission_statement_line", entity_id=line_id,
                      actor_user_id=_actor(principal, actor_user_id), request_id=_rid(request_id),
                      metadata={"commission_id": commission_id, "status": status})
    return {"line_id": line_id, "matched": True, "commission_id": commission_id, "status": status}


def reconcile_statement(principal, statement_id, *, actor_user_id=None, request_id=None):
    """Auto-reconcile every unmatched line on a statement, then roll the statement's own
    status up (reconciled / partially_reconciled / imported)."""
    _require(principal, "insurance.commissions.write")
    with engine.connect() as c:
        if c.execute(select(insurance_commission_statements.c.id).where(
                insurance_commission_statements.c.id == statement_id)).scalar_one_or_none() is None:
            raise CommissionNotFound("Statement not found.")
        line_ids = [r[0] for r in c.execute(select(insurance_commission_statement_lines.c.id).where(
            insurance_commission_statement_lines.c.statement_id == statement_id,
            insurance_commission_statement_lines.c.status == "unmatched"))]
    matched = 0
    for lid in line_ids:
        if reconcile_line(principal, lid, actor_user_id=actor_user_id, request_id=request_id).get("matched"):
            matched += 1
    with engine.begin() as c:
        statuses = [r[0] for r in c.execute(select(insurance_commission_statement_lines.c.status).where(
            insurance_commission_statement_lines.c.statement_id == statement_id))]
        if statuses and all(s == "reconciled" for s in statuses):
            st = "reconciled"
        elif any(s == "reconciled" for s in statuses):
            st = "partially_reconciled"
        else:
            st = "imported"
        c.execute(insurance_commission_statements.update().where(
            insurance_commission_statements.c.id == statement_id).values(status=st, updated_at=_now()))
    # Audit the statement-level roll-up (reconciliation status change) as its own event, in
    # addition to the per-line events reconcile_line already writes.
    write_audit_event(action="insurance.commission.statement_reconciled",
                      entity_type="insurance_commission_statement", entity_id=statement_id,
                      actor_user_id=_actor(principal, actor_user_id), request_id=_rid(request_id),
                      metadata={"status": st, "lines_matched": matched, "lines_total": len(line_ids)})
    return {"statement_id": statement_id, "lines_matched": matched, "status": st,
            "lines_total": len(line_ids)}


def get_statement(principal, statement_id):
    _require(principal, "insurance.commissions.read")
    with engine.connect() as c:
        row = c.execute(select(insurance_commission_statements).where(
            insurance_commission_statements.c.id == statement_id)).mappings().one_or_none()
        if row is None:
            raise CommissionNotFound("Statement not found.")
        lines = [dict(r) for r in c.execute(select(insurance_commission_statement_lines).where(
            insurance_commission_statement_lines.c.statement_id == statement_id)
            .order_by(insurance_commission_statement_lines.c.id)).mappings()]
        return {**dict(row),
                "carrier_name": ins._entity_name(c, "organization", row["carrier_id"]),
                "lines": lines}


def list_statements(principal, *, carrier_id=None, status=None, limit=200):
    _require(principal, "insurance.commissions.read")
    query = select(insurance_commission_statements).order_by(insurance_commission_statements.c.id.desc())
    if carrier_id:
        query = query.where(insurance_commission_statements.c.carrier_id == carrier_id)
    if status:
        query = query.where(insurance_commission_statements.c.status == status)
    with engine.connect() as c:
        out = []
        for row in c.execute(query.limit(limit)).mappings():
            d = dict(row)
            d["carrier_name"] = ins._entity_name(c, "organization", row["carrier_id"])
            out.append(d)
    return out
