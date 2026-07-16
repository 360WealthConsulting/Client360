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

    Everything here is DERIVED, in one pass, from the canonical commission ledger
    (``insurance_commissions``) plus its producer split data — there is no persisted
    aggregate and no second source of truth (``service_revenue`` is never written by the
    commission ledger; it stays an operational projection). ``expected_*`` come from each
    entry's ``expected_amount``; ``received_*`` / actuals come from ``received_amount`` (which
    already reflects posted receipts AND every adjustment / reversal / chargeback applied via
    ``record_adjustment``). Because it is a pure re-sum over the ledger, the rollup is
    idempotent, never double-counts on repeated runs, and a correction flows straight through.

    Producer payouts (individual producers) and agency-retained revenue (organization
    producers — agency / broker-of-record / override) are split by ``producer_entity_type``.
    Money reconciliation and revenue reporting only; no compliance determination.
    """
    entries = com.list_commissions(principal, limit=None)  # full scoped ledger — no cap

    def money(x):
        return Decimal(str(x)) if x is not None else Decimal("0")

    open_statuses = ("expected", "partial", "variance")
    by_schedule, by_org, by_producer = {}, {}, {}
    producer_payout = {"expected": Decimal("0"), "received": Decimal("0")}
    agency_retained = {"expected": Decimal("0"), "received": Decimal("0")}
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
        # producer payouts vs agency-retained, both derived from the ledger + split data
        prod_key = f"{e['producer_entity_type']}:{e['producer_entity_id']}"
        prod = by_producer.setdefault(prod_key, {"expected": Decimal("0"), "received": Decimal("0")})
        prod["expected"] += exp
        prod["received"] += rec
        bucket = producer_payout if e["producer_entity_type"] == "user" else agency_retained
        bucket["expected"] += exp
        bucket["received"] += rec

    def flatten(bucket):
        return {k: {"expected": float(v["expected"]), "received": float(v["received"])}
                for k, v in bucket.items()}

    def pair(bucket):
        return {"expected": float(bucket["expected"]), "received": float(bucket["received"])}

    return {
        "revenue_category": "insurance_commissions",
        "entry_count": len(entries),
        "by_status": dict(Counter(e["status"] for e in entries)),
        "expected_total": float(expected_total),
        "received_total": float(received_total),
        "outstanding_total": float(outstanding_total),
        "variance_total": float(received_total - expected_total),
        "producer_payouts": pair(producer_payout),
        "agency_retained": pair(agency_retained),
        "by_schedule": flatten(by_schedule),
        "by_organization": flatten(by_org),
        "by_producer": flatten(by_producer),
    }


# ============================================================================
# Phase 8 — consolidated operations dashboard + operational summaries.
# Firm-internal STAFF reporting only (never the client portal). Extends this module;
# reuses the shared Exception Engine, Work Management, and portal grants — no parallel
# reporting engine, dashboard framework, authorization system, or record-scope model.
# Authorization is applied BEFORE aggregation (every section derives from a scoped list),
# and each optional section is included only if the viewer holds its capability. Operational
# counts / workflow status / financial reconciliation ONLY — no compliance determination (AD-5).
# ============================================================================

def _can(principal, cap):
    return principal is not None and principal.can(cap)


def exception_summary(principal):
    """Insurance operational exception counts within record scope. Reuses the shared engine's
    scope-filtered list (authorization before aggregation) — operational only, no compliance
    conclusion. Requires exception.read."""
    from sqlalchemy import select

    from app.db import engine, exception_types
    from app.services import exception_engine as ee
    rows = ee.list_exceptions(principal, domain="insurance")  # record-scope enforced here
    with engine.connect() as c:
        code_by_id = {r[0]: r[1] for r in c.execute(select(
            exception_types.c.id, exception_types.c.code).where(
            exception_types.c.domain == "insurance"))}
    open_rows = [r for r in rows if r["status"] not in ee.CLOSED_STATUSES]
    return {
        "total": len(rows),
        "open": len(open_rows),
        "by_code": dict(Counter(code_by_id.get(r["exception_type_id"], "unknown") for r in open_rows)),
        "by_severity": dict(Counter(r["severity"] for r in open_rows)),
        "by_status": dict(Counter(r["status"] for r in rows)),
    }


def work_queue_report(principal):
    """Insurance work-queue depths — reuses Work Management ``work_items`` (scope-filtered) and
    the existing queue criteria (the same counting the shared work dashboard uses). No new queue
    engine. Requires work.read."""
    from sqlalchemy import select

    from app.db import engine, work_queues
    from app.services.work_intelligence import queue_items
    from app.services.work_management import work_items
    items = work_items(principal)  # scope-filtered; includes insurance
    with engine.connect() as c:
        queues = c.execute(select(work_queues.c.code, work_queues.c.name, work_queues.c.criteria)
                           .where(work_queues.c.code.like("insurance_%"), work_queues.c.active.is_(True))
                           .order_by(work_queues.c.name)).mappings().all()
    return [{"code": q["code"], "name": q["name"], "count": len(queue_items(items, q["criteria"] or {}))}
            for q in queues]


def portal_activity_report(principal):
    """Firm-internal policyholder-portal adoption for insurance (oversight metric — NOT
    client-facing). Counts active portal grants that allow the ``insurance`` permission and the
    policies exposed through them. Requires record.read_all (oversight)."""
    from sqlalchemy import func, or_, select

    from app.db import engine, insurance_policies, portal_access_grants
    today = date.today()
    with engine.connect() as c:
        grants = c.execute(select(portal_access_grants).where(
            portal_access_grants.c.effective_date <= today,
            or_(portal_access_grants.c.inactive_date.is_(None),
                portal_access_grants.c.inactive_date >= today))).mappings().all()
    ins_grants = [g for g in grants if (g["permissions"] or {}).get("insurance")]
    person_ids = {g["person_id"] for g in ins_grants if g["person_id"]}
    household_ids = {g["household_id"] for g in ins_grants if g["household_id"]}
    org_ids = {g["organization_id"] for g in ins_grants if g["organization_id"]}
    clauses = []
    if person_ids:
        clauses.append(insurance_policies.c.person_id.in_(person_ids))
    if household_ids:
        clauses.append(insurance_policies.c.household_id.in_(household_ids))
    if org_ids:
        clauses.append(insurance_policies.c.organization_id.in_(org_ids))
    exposed = 0
    if clauses:
        with engine.connect() as c:
            exposed = c.execute(select(func.count()).select_from(insurance_policies)
                                .where(or_(*clauses))).scalar_one()
    return {
        "portal_accounts_with_insurance": len({g["portal_account_id"] for g in ins_grants}),
        "grants_with_insurance": len(ins_grants),
        "policies_exposed": exposed,
    }


def operations_dashboard(principal):
    """Consolidated, firm-internal insurance operations dashboard — proportional to the viewer's
    capabilities and record scope. Reuses the existing per-domain reports + shared primitives; no
    parallel engine. Operational / workflow / financial reporting ONLY — no compliance
    determination or metric (AD-5). Served as a STAFF surface, never through the client portal."""
    sections = {
        "pipeline": pipeline_report(principal),   # insurance.read (route-gated)
        "reviews": review_report(principal),
    }
    if _can(principal, "exception.read"):
        sections["exceptions"] = exception_summary(principal)
    if _can(principal, "work.read"):
        sections["work_queues"] = work_queue_report(principal)
    if _can(principal, "insurance.commissions.read"):
        sections["commissions"] = commission_report(principal)
    if _can(principal, "insurance.licensing.read"):
        sections["licensing"] = licensing_report(principal)
    if _can(principal, "record.read_all"):
        sections["portal_adoption"] = portal_activity_report(principal)
    return {"boundary": "firm_internal_staff",
            "sections_included": sorted(sections.keys()),
            "sections": sections}
