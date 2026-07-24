"""Compliance Intelligence analytics (Phase D.47) — LOW-CARDINALITY aggregates only. Never client
identifiers, reviewer names, or supervisory evidence. These feed the platform Analytics registry + internal
diagnostics.
"""
from __future__ import annotations

from . import registry, stats


def compliance_metrics(principal=None) -> dict:
    s = stats.compliance_stats()
    return {
        "reviews_composed": s.get("reviews_composed", 0),
        "exceptions_composed": s.get("exceptions_composed", 0),
        "dashboards": s.get("dashboards", 0),
        "overdue_reviews": s.get("overdue_reviews", 0),
        "suppressed": s.get("suppressed", 0),
        "authorization_failures": s.get("authorization_failures", 0),
        "adapter_failures": s.get("adapter_failures", 0),
        "avg_compose_ms": s.get("avg_compose_ms", 0.0),
        "by_review_type": s.get("by_review_type", {}),
        "by_severity": s.get("by_severity", {}),
        "review_types": registry.coverage()["review_types"],
        "exception_types": registry.coverage()["exception_types"],
    }


# --- readers for the platform Analytics registry (in-process counters; no DB, no PII) ---

def supervisory_reviews_composed(principal) -> int:
    return int(stats.compliance_stats().get("reviews_composed", 0))


def supervisory_exceptions_composed(principal) -> int:
    return int(stats.compliance_stats().get("exceptions_composed", 0))


def supervisory_dashboards(principal) -> int:
    return int(stats.compliance_stats().get("dashboards", 0))


def supervisory_authorization_failures(principal) -> int:
    return int(stats.compliance_stats().get("authorization_failures", 0))
