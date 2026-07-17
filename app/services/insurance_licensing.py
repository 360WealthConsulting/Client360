"""Insurance producer licensing & continuing-education RECORDS (Phase 4, non-regulated).

Release 0.10.0, Phase 4. Firm-internal producer (user) licensing and CE records —
data capture only. Capability-gated (``insurance.licensing.read`` / ``.write``); this
is producer/firm data, NOT client-record-scoped, so no household/person scope applies.
Every mutation writes a shared audit event.

This module records what staff enter and nothing more. It performs NO licensing
*validation* (whether a producer may sell a product in a state), NO CE *determination*
(whether a CE requirement is satisfied), and blocks nothing. Those regulated
determinations remain behind the AD-5 compliance gate. Date-driven expiry reminders
live in ``insurance_detectors`` (operational calendar, not a compliance conclusion).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import engine, insurance_ce_records, insurance_licenses, users
from app.security.audit import write_audit_event


class LicensingError(RuntimeError):
    """Invalid licensing/CE input."""


class LicensingNotFound(LicensingError):
    """The requested licensing/CE record does not exist."""


def _now():
    return datetime.now(UTC)


def _require(principal, cap):
    if principal is not None and not principal.can(cap):
        raise PermissionError(f"Missing capability {cap}")


def _rid(request_id):
    return request_id or f"insurance-{uuid.uuid4()}"


def _actor(principal, actor_user_id):
    if actor_user_id is not None:
        return actor_user_id
    return getattr(principal, "user_id", None)


def _producer_name(c, producer_user_id):
    if not producer_user_id:
        return None
    return c.execute(select(users.c.display_name).where(
        users.c.id == producer_user_id)).scalar_one_or_none()


# --- licenses ----------------------------------------------------------------

_LICENSE_FIELDS = ("state", "license_number", "npn", "lines", "status",
                   "issue_date", "expiry_date", "notes")


def record_license(principal, *, producer_user_id, state, license_number=None, npn=None,
                   lines=None, status="active", issue_date=None, expiry_date=None,
                   notes=None, actor_user_id=None, request_id=None):
    """Capture a producer licensing record. No validation of authority — staff-entered."""
    _require(principal, "insurance.licensing.write")
    if not producer_user_id or not state:
        raise LicensingError("A license needs a producer and a state.")
    with engine.begin() as c:
        lid = c.execute(insurance_licenses.insert().values(
            producer_user_id=producer_user_id, state=state, license_number=license_number,
            npn=npn, lines=lines, status=status, issue_date=issue_date, expiry_date=expiry_date,
            notes=notes, created_by_user_id=_actor(principal, actor_user_id),
        ).returning(insurance_licenses.c.id)).scalar_one()
    write_audit_event(action="insurance.license.recorded", entity_type="insurance_license",
                      entity_id=lid, actor_user_id=_actor(principal, actor_user_id),
                      request_id=_rid(request_id),
                      metadata={"producer_user_id": producer_user_id, "state": state, "status": status})
    return {"id": lid}


def update_license(principal, license_id, *, actor_user_id=None, request_id=None, **fields):
    _require(principal, "insurance.licensing.write")
    values = {k: v for k, v in fields.items() if k in _LICENSE_FIELDS and v is not None}
    with engine.begin() as c:
        if c.execute(select(insurance_licenses.c.id).where(
                insurance_licenses.c.id == license_id)).scalar_one_or_none() is None:
            raise LicensingNotFound("License not found.")
        if values:
            c.execute(insurance_licenses.update().where(
                insurance_licenses.c.id == license_id).values(updated_at=_now(), **values))
    write_audit_event(action="insurance.license.updated", entity_type="insurance_license",
                      entity_id=license_id, actor_user_id=_actor(principal, actor_user_id),
                      request_id=_rid(request_id), metadata={"fields": sorted(values)})
    return {"id": license_id}


def get_license(principal, license_id):
    _require(principal, "insurance.licensing.read")
    with engine.connect() as c:
        row = c.execute(select(insurance_licenses).where(
            insurance_licenses.c.id == license_id)).mappings().one_or_none()
        if row is None:
            raise LicensingNotFound("License not found.")
        d = dict(row)
        d["producer_name"] = _producer_name(c, row["producer_user_id"])
        return d


def list_licenses(principal, *, producer_user_id=None, state=None, status=None, limit=500):
    _require(principal, "insurance.licensing.read")
    query = select(insurance_licenses).order_by(insurance_licenses.c.expiry_date)
    if producer_user_id:
        query = query.where(insurance_licenses.c.producer_user_id == producer_user_id)
    if state:
        query = query.where(insurance_licenses.c.state == state)
    if status:
        query = query.where(insurance_licenses.c.status == status)
    with engine.connect() as c:
        out = []
        for row in c.execute(query.limit(limit)).mappings():
            d = dict(row)
            d["producer_name"] = _producer_name(c, row["producer_user_id"])
            out.append(d)
    return out


# --- continuing education ----------------------------------------------------

_CE_FIELDS = ("state", "period_start", "period_end", "credits_required",
              "credits_completed", "status", "notes")


def record_ce(principal, *, producer_user_id, state=None, period_start=None, period_end=None,
              credits_required=None, credits_completed=None, status="in_progress",
              notes=None, actor_user_id=None, request_id=None):
    """Capture a CE tracking record. credits_* are staff-entered figures; the platform
    does NOT conclude whether the CE requirement is satisfied (that stays AD-5 gated)."""
    _require(principal, "insurance.licensing.write")
    if not producer_user_id:
        raise LicensingError("A CE record needs a producer.")
    with engine.begin() as c:
        cid = c.execute(insurance_ce_records.insert().values(
            producer_user_id=producer_user_id, state=state, period_start=period_start,
            period_end=period_end, credits_required=credits_required,
            credits_completed=credits_completed, status=status, notes=notes,
            created_by_user_id=_actor(principal, actor_user_id),
        ).returning(insurance_ce_records.c.id)).scalar_one()
    write_audit_event(action="insurance.ce.recorded", entity_type="insurance_ce_record",
                      entity_id=cid, actor_user_id=_actor(principal, actor_user_id),
                      request_id=_rid(request_id),
                      metadata={"producer_user_id": producer_user_id, "state": state, "status": status})
    return {"id": cid}


def update_ce(principal, ce_id, *, actor_user_id=None, request_id=None, **fields):
    _require(principal, "insurance.licensing.write")
    values = {k: v for k, v in fields.items() if k in _CE_FIELDS and v is not None}
    with engine.begin() as c:
        if c.execute(select(insurance_ce_records.c.id).where(
                insurance_ce_records.c.id == ce_id)).scalar_one_or_none() is None:
            raise LicensingNotFound("CE record not found.")
        if values:
            c.execute(insurance_ce_records.update().where(
                insurance_ce_records.c.id == ce_id).values(updated_at=_now(), **values))
    write_audit_event(action="insurance.ce.updated", entity_type="insurance_ce_record",
                      entity_id=ce_id, actor_user_id=_actor(principal, actor_user_id),
                      request_id=_rid(request_id), metadata={"fields": sorted(values)})
    return {"id": ce_id}


def get_ce(principal, ce_id):
    _require(principal, "insurance.licensing.read")
    with engine.connect() as c:
        row = c.execute(select(insurance_ce_records).where(
            insurance_ce_records.c.id == ce_id)).mappings().one_or_none()
        if row is None:
            raise LicensingNotFound("CE record not found.")
        d = dict(row)
        d["producer_name"] = _producer_name(c, row["producer_user_id"])
        return d


def list_ce(principal, *, producer_user_id=None, status=None, limit=500):
    _require(principal, "insurance.licensing.read")
    query = select(insurance_ce_records).order_by(insurance_ce_records.c.period_end)
    if producer_user_id:
        query = query.where(insurance_ce_records.c.producer_user_id == producer_user_id)
    if status:
        query = query.where(insurance_ce_records.c.status == status)
    with engine.connect() as c:
        out = []
        for row in c.execute(query.limit(limit)).mappings():
            d = dict(row)
            d["producer_name"] = _producer_name(c, row["producer_user_id"])
            out.append(d)
    return out
