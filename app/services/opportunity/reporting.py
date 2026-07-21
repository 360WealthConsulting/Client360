"""Pipeline reporting (Phase D.13).

Authorization-filtered aggregates over the principal's in-scope pipeline (scope resolved by
the service before aggregation — never firm-wide unless ``record.read_all``). Two report
surfaces: a general pipeline report (``opportunity.report``) and a sensitive revenue forecast
(``opportunity.forecast``). No new persistence; pure read aggregation.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal

from app.services.opportunity import service as svc

_AGING_BUCKETS = ((0, 30, "0-30 days"), (31, 90, "31-90 days"),
                  (91, 180, "91-180 days"), (181, 10_000, "180+ days"))


def _num(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def pipeline_report(principal, *, today=None) -> dict:
    """Pipeline by advisor / stage / service line / referral source, win rate, loss reasons,
    conversion, and aging. Aggregated over in-scope opportunities."""
    rows = svc.all_in_scope(principal)
    open_rows = [o for o in rows if o["status"] == "open"]
    won = sum(1 for o in rows if o["status"] == "won")
    lost = sum(1 for o in rows if o["status"] == "lost")
    closed = won + lost

    by_stage = Counter(o["stage_id"] for o in open_rows)
    by_advisor = Counter(o["primary_advisor_id"] for o in open_rows)
    by_service_line = Counter((o["primary_service_line"] or "unspecified") for o in rows)
    by_source = Counter((o["source"] or "unspecified") for o in rows)
    loss_reasons = Counter((o["loss_reason"] or "unspecified")
                           for o in rows if o["status"] == "lost")

    # Aging of OPEN opportunities by days since creation.
    aging = Counter()
    if today is not None:
        for o in open_rows:
            age = (today - o["created_at"].date()).days
            for lo, hi, label in _AGING_BUCKETS:
                if lo <= age <= hi:
                    aging[label] += 1
                    break

    return {
        "counts": {"total": len(rows), "open": len(open_rows), "won": won, "lost": lost,
                   "dormant": sum(1 for o in rows if o["status"] == "dormant"),
                   "cancelled": sum(1 for o in rows if o["status"] == "cancelled")},
        "open_value": float(sum(_num(o["expected_revenue"]) for o in open_rows)),
        "by_stage": dict(by_stage),
        "by_advisor": dict(by_advisor),
        "by_service_line": dict(by_service_line),
        "by_source": dict(by_source),
        "win_rate": (round(won / closed, 4) if closed else None),
        "conversion": {"won": won, "lost": lost, "closed": closed},
        "loss_reasons": dict(loss_reasons),
        "aging": dict(aging),
    }


def forecast_report(principal) -> dict:
    """Sensitive revenue forecast (gated by ``opportunity.forecast`` at the route). Expected
    revenue and probability-weighted forecast over OPEN opportunities, by close month and by
    advisor. Never exposed without the forecast capability."""
    open_rows = svc.all_in_scope(principal, statuses=("open",))
    expected_total = sum(_num(o["expected_revenue"]) for o in open_rows)
    weighted_total = sum(_num(o["expected_revenue"]) * _num(o["probability"]) / Decimal("100")
                         for o in open_rows)

    by_month: dict[str, float] = {}
    by_advisor: dict[int, float] = {}
    for o in open_rows:
        rev = _num(o["expected_revenue"])
        if o["expected_close_date"] is not None:
            key = o["expected_close_date"].strftime("%Y-%m")
            by_month[key] = by_month.get(key, 0.0) + float(rev)
        adv = o["primary_advisor_id"]
        by_advisor[adv] = by_advisor.get(adv, 0.0) + float(rev)

    return {
        "open_count": len(open_rows),
        "expected_revenue_total": float(expected_total),
        "weighted_forecast_total": round(float(weighted_total), 2),
        "expected_revenue_by_close_month": dict(sorted(by_month.items())),
        "expected_revenue_by_advisor": by_advisor,
    }
