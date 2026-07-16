"""Insurance producer licensing & CE — NON-REGULATED records + expiry reminders (Phase 4).

Pins licensing/CE record capture (firm-internal, capability-gated), the date-driven
expiry detectors (idempotent, auto-resolving through the SHARED Exception Engine — no
second engine), operational licensing metrics, and capability gating. Also asserts the
compliance-gated behaviors stay absent: no licensing *validation* (is-a-producer-licensed)
and no CE *satisfaction* determination ship here — those remain behind the AD-5 gate.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db import engine, exceptions, users
from app.security.models import Principal
from app.services import insurance_detectors as det
from app.services import insurance_licensing as lic
from app.services import insurance_reporting

FULL = frozenset({"insurance.licensing.read", "insurance.licensing.write",
                  "record.read_all", "exception.read", "exception.write"})


def _p(caps=FULL):
    return Principal(1, "a@e.com", "A", caps)


def _sfx():
    return uuid.uuid4().hex


def _producer():
    with engine.begin() as c:
        return c.execute(users.insert().values(
            email=f"p-{_sfx()}@e.com", normalized_email=f"p-{_sfx()}@e.com", display_name="Producer P",
            auth_subject=f"s-{_sfx()}", status="active").returning(users.c.id)).scalar_one()


TODAY = date(2026, 7, 15)


# --- records -----------------------------------------------------------------

def test_record_and_read_license():
    pid = _producer()
    out = lic.record_license(_p(), producer_user_id=pid, state="CA", license_number="X1",
                             lines=["life", "variable"], expiry_date=date(2027, 1, 1))
    got = lic.get_license(_p(), out["id"])
    assert got["state"] == "CA" and got["producer_name"] == "Producer P" and got["lines"] == ["life", "variable"]


def test_record_and_read_ce():
    pid = _producer()
    out = lic.record_ce(_p(), producer_user_id=pid, state="CA", period_end=date(2027, 6, 30),
                        credits_required=24, credits_completed=8)
    got = lic.get_ce(_p(), out["id"])
    assert got["status"] == "in_progress" and got["credits_required"] == 24


def test_update_license_status():
    pid = _producer()
    lid = lic.record_license(_p(), producer_user_id=pid, state="TX")["id"]
    lic.update_license(_p(), lid, status="inactive")
    assert lic.get_license(_p(), lid)["status"] == "inactive"


# --- expiry detector (operational calendar) ----------------------------------

def test_expiring_license_raises_and_is_idempotent():
    pid = _producer()
    lid = lic.record_license(_p(), producer_user_id=pid, state="CA", status="active",
                             expiry_date=TODAY + timedelta(days=30))["id"]  # inside 60-day window
    first = det.run_insurance_licensing_scan(actor_user_id=1, today=TODAY)
    assert first["exceptions_opened"] >= 1
    second = det.run_insurance_licensing_scan(actor_user_id=1, today=TODAY)
    assert second["exceptions_opened"] == 0  # dedupe: no duplicate
    with engine.connect() as c:
        n = c.execute(select(exceptions.c.id).where(
            exceptions.c.dedupe_key == f"ins:license_expiring:{lid}")).scalars().all()
    assert len(n) == 1


def test_far_future_license_does_not_raise():
    pid = _producer()
    lid = lic.record_license(_p(), producer_user_id=pid, state="CA", status="active",
                             expiry_date=TODAY + timedelta(days=400))["id"]
    det.run_insurance_licensing_scan(actor_user_id=1, today=TODAY)
    with engine.connect() as c:
        n = c.execute(select(exceptions.c.id).where(
            exceptions.c.dedupe_key == f"ins:license_expiring:{lid}")).scalars().all()
    assert n == []


def test_deactivating_expiring_license_auto_resolves():
    pid = _producer()
    lid = lic.record_license(_p(), producer_user_id=pid, state="CA", status="active",
                             expiry_date=TODAY + timedelta(days=10))["id"]
    det.run_insurance_licensing_scan(actor_user_id=1, today=TODAY)
    lic.update_license(_p(), lid, status="inactive")  # condition clears
    result = det.run_insurance_licensing_scan(actor_user_id=1, today=TODAY)
    assert result["exceptions_resolved"] >= 1
    with engine.connect() as c:
        status = c.execute(select(exceptions.c.status).where(
            exceptions.c.dedupe_key == f"ins:license_expiring:{lid}")).scalar_one()
    assert status in ("resolved", "cancelled")


def test_ce_period_ending_raises():
    pid = _producer()
    cid = lic.record_ce(_p(), producer_user_id=pid, status="in_progress",
                        period_end=TODAY + timedelta(days=30))["id"]  # inside 90-day window
    det.run_insurance_licensing_scan(actor_user_id=1, today=TODAY)
    with engine.connect() as c:
        n = c.execute(select(exceptions.c.id).where(
            exceptions.c.dedupe_key == f"ins:ce_period_ending:{cid}")).scalars().all()
    assert len(n) == 1


# --- reporting ---------------------------------------------------------------

def test_licensing_report_operational_counts_only():
    pid = _producer()
    lic.record_license(_p(), producer_user_id=pid, state="CA", status="active",
                       expiry_date=TODAY + timedelta(days=20))
    report = insurance_reporting.licensing_report(_p(), today=TODAY)
    assert set(report) == {"license_count", "licenses_by_status", "licenses_expiring",
                           "ce_count", "ce_by_status"}
    assert report["licenses_expiring"] >= 1


# --- capability gating -------------------------------------------------------

def test_licensing_requires_capability():
    pid = _producer()
    no_caps = Principal(2, "b@e.com", "B", frozenset())
    with pytest.raises(PermissionError):
        lic.list_licenses(no_caps)
    with pytest.raises(PermissionError):
        lic.record_license(no_caps, producer_user_id=pid, state="CA")


# --- the compliance gate: no regulated determination ships -------------------

def test_no_licensing_validation_or_ce_determination_functions():
    import inspect as _inspect
    regulated = {
        "validate_license", "validate_licensing", "is_licensed", "check_licensing",
        "check_ce", "evaluate_ce", "determine_ce", "ce_satisfied", "is_ce_complete",
        "approve_compliance", "compliance_decision", "regulatory_decision",
    }
    verbs = ("validate", "determine", "recommend", "certif", "satisf")
    for mod in (lic, det, insurance_reporting):
        defined = {n for n, obj in vars(mod).items()
                   if _inspect.isfunction(obj) and obj.__module__ == mod.__name__}
        assert defined & regulated == set(), f"regulated logic leaked into {mod.__name__}"
        assert not [n for n in defined for v in verbs if v in n], \
            f"a determination verb leaked into {mod.__name__}"


def test_licensing_scan_result_has_no_compliance_fields():
    result = det.run_insurance_licensing_scan(actor_user_id=1, today=TODAY)
    assert set(result) == {"exceptions_opened", "exceptions_reopened", "exceptions_resolved",
                           "failures", "failure_detail"}
