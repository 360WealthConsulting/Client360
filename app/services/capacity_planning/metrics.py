"""Enterprise Capacity Planning analytics readers (Phase D.61).

These are LOW-CARDINALITY operational counters ABOUT the capacity layer itself (how many
dashboards/panels/summaries were composed, failures) — registered into the SINGLE, existing Analytics
Registry. This layer defines NO workforce / capacity / utilization business metrics (those stay owned by their
authoritative owners) and NO second metrics registry. Never employee details, payroll, HR records, calendar
contents, time entries, or sensitive staffing data.
"""
from __future__ import annotations

from . import registry, stats


def capacity_planning_metrics(principal=None) -> dict:
    s = stats.capacity_stats()
    cov = registry.coverage()
    return {
        "dashboards_composed": s.get("dashboards_composed", 0),
        "panels_composed": s.get("panels_composed", 0),
        "summaries_composed": s.get("summaries_composed", 0),
        "aggregation_failures": s.get("aggregation_failures", 0),
        "authorization_failures": s.get("authorization_failures", 0),
        "restricted_panels": s.get("restricted_panels", 0),
        "avg_compose_ms": s.get("avg_compose_ms", 0.0),
        "by_dashboard": s.get("by_dashboard", {}),
        "workforce_classes": cov["workforce_classes"],
        "capacity_categories": cov["capacity_categories"],
        "utilization_categories": cov["utilization_categories"],
        "dashboards": cov["dashboards"],
        "panels": cov["panels"],
    }


# --- readers for the platform Analytics registry (in-process counters; no DB, no PII) ---

def capacity_dashboards_composed(principal) -> int:
    return int(stats.capacity_stats().get("dashboards_composed", 0))


def capacity_panels_composed(principal) -> int:
    return int(stats.capacity_stats().get("panels_composed", 0))


def capacity_panel_failures(principal) -> int:
    return int(stats.capacity_stats().get("aggregation_failures", 0))


def capacity_authorization_failures(principal) -> int:
    return int(stats.capacity_stats().get("authorization_failures", 0))
