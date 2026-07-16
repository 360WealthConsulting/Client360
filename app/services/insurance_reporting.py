"""Insurance operational reporting (Release 0.10.0, Phase 2, non-regulated).

Pipeline counts only — cases and policies by status, and outstanding requirements
across the principal's scoped cases. Authorization-filtered before aggregation
(reuses the scoped list services). This is operational management reporting; it
contains NO compliance metrics (no suitability/replacement/1035/licensing/CE rates
or determinations) — those remain behind the AD-5 gate.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

from app.services import insurance as ins
from app.services import insurance_commissions as com
from app.services import insurance_licensing as lic


def pipeline_report(principal):
    """Operational pipeline snapshot within the principal's record scope."""
    cases = ins.list_cases(principal)          # scope-filtered
    policies = ins.list_policies(principal)    # scope-filtered

    open_requirements = 0
    for case in cases:
        open_requirements += len(
            ins.list_requirements(principal, case_id=case["id"], open_only=True))

    return {
        "case_count": len(cases),
        "cases_by_status": dict(Counter(c["status"] for c in cases)),
        "policy_count": len(policies),
        "policies_by_status": dict(Counter(p["status"] for p in policies)),
        "open_requirements": open_requirements,
    }


def review_report(principal):
    """Servicing-review metrics within the principal's record scope. Operational
    only — completion rate and overdue/deferred counts. NO compliance metrics
    (no suitability/replacement/1035/licensing/CE rates); those stay behind AD-5."""
    reviews = ins.list_reviews(principal)  # scope-filtered
    by_status = Counter(r["status"] for r in reviews)
    total = len(reviews)
    completed = by_status.get("completed", 0)
    return {
        "total": total,
        "by_status": dict(by_status),
        "completed": completed,
        "overdue": by_status.get("overdue", 0),
        "deferred": by_status.get("deferred", 0),
        "completion_rate": round(completed / total, 3) if total else 0.0,
    }


def licensing_report(principal, *, today=None, window_days=60):
    """Producer licensing & CE operational counts. Records + upcoming-expiry counts
    only — the platform makes NO licensing-validation or CE-satisfaction determination
    (those stay behind the AD-5 gate). Requires insurance.licensing.read."""
    licenses = lic.list_licenses(principal)
    ce = lic.list_ce(principal)
    horizon = (today or date.today()) + timedelta(days=window_days)
    expiring = sum(1 for row in licenses
                   if row["status"] == "active" and row["expiry_date"] and row["expiry_date"] <= horizon)
    return {
        "license_count": len(licenses),
        "licenses_by_status": dict(Counter(row["status"] for row in licenses)),
        "licenses_expiring": expiring,
        "ce_count": len(ce),
        "ce_by_status": dict(Counter(row["status"] for row in ce)),
    }


def commission_report(principal):
    """Operational commission reconciliation + revenue rollup within record scope.

    Expected vs received, outstanding, and variance totals, broken out by schedule and by
    organization, tagged with the ``insurance_commissions`` revenue category. This is money
    reconciliation and revenue reporting — it makes no compliance determination.
    """
    entries = com.list_commissions(principal)  # scope-filtered by policy

    def money(x):
        return Decimal(str(x)) if x is not None else Decimal("0")

    open_statuses = ("expected", "partial", "variance")
    by_schedule, by_org = {}, {}
    expected_total = received_total = outstanding_total = Decimal("0")
    for e in entries:
        exp, rec = money(e["expected_amount"]), money(e["received_amount"])
        expected_total += exp
        received_total += rec
        if e["status"] in open_statuses and exp > rec:
            outstanding_total += exp - rec
        sched = by_schedule.setdefault(e["schedule"], {"expected": Decimal("0"), "received": Decimal("0")})
        sched["expected"] += exp
        sched["received"] += rec
        org_key = e["organization_id"] if e["organization_id"] is not None else "unassigned"
        org = by_org.setdefault(org_key, {"expected": Decimal("0"), "received": Decimal("0")})
        org["expected"] += exp
        org["received"] += rec

    def flatten(bucket):
        return {k: {"expected": float(v["expected"]), "received": float(v["received"])}
                for k, v in bucket.items()}

    return {
        "revenue_category": "insurance_commissions",
        "entry_count": len(entries),
        "by_status": dict(Counter(e["status"] for e in entries)),
        "expected_total": float(expected_total),
        "received_total": float(received_total),
        "outstanding_total": float(outstanding_total),
        "variance_total": float(received_total - expected_total),
        "by_schedule": flatten(by_schedule),
        "by_organization": flatten(by_org),
    }
