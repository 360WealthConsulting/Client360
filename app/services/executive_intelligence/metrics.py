"""Executive Reporting analytics readers (Phase D.48).

These are LOW-CARDINALITY operational counters ABOUT the reporting layer itself (how many dashboards/widgets
were composed, failures) — registered into the SINGLE, existing Analytics Registry. This layer defines NO
business metrics and NO second metrics registry; every KPI value comes from ``analytics.metrics``. Never
client identifiers or metric values.
"""
from __future__ import annotations

from . import registry, stats


def reporting_metrics(principal=None) -> dict:
    s = stats.reporting_stats()
    return {
        "dashboards_composed": s.get("dashboards_composed", 0),
        "widgets_composed": s.get("widgets_composed", 0),
        "aggregation_failures": s.get("aggregation_failures", 0),
        "authorization_failures": s.get("authorization_failures", 0),
        "restricted_widgets": s.get("restricted_widgets", 0),
        "avg_compose_ms": s.get("avg_compose_ms", 0.0),
        "by_dashboard": s.get("by_dashboard", {}),
        "dashboards": registry.coverage()["dashboards"],
        "widgets": registry.coverage()["widgets"],
    }


# --- readers for the platform Analytics registry (in-process counters; no DB, no PII) ---

def executive_dashboards_composed(principal) -> int:
    return int(stats.reporting_stats().get("dashboards_composed", 0))


def executive_widgets_composed(principal) -> int:
    return int(stats.reporting_stats().get("widgets_composed", 0))


def executive_widget_failures(principal) -> int:
    return int(stats.reporting_stats().get("aggregation_failures", 0))


def executive_authorization_failures(principal) -> int:
    return int(stats.reporting_stats().get("authorization_failures", 0))
