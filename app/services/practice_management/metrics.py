"""Practice Management analytics readers (Phase D.49).

These are LOW-CARDINALITY operational counters ABOUT the practice-management layer itself (how many
dashboards/panels/summaries were composed, failures) — registered into the SINGLE, existing Analytics
Registry. This layer defines NO business metrics and NO second metrics registry; every utilization number
comes from the authoritative owners. Never resource names, client identifiers, or workload values.
"""
from __future__ import annotations

from . import registry, stats


def practice_metrics(principal=None) -> dict:
    s = stats.practice_stats()
    return {
        "dashboards_composed": s.get("dashboards_composed", 0),
        "panels_composed": s.get("panels_composed", 0),
        "summaries_composed": s.get("summaries_composed", 0),
        "aggregation_failures": s.get("aggregation_failures", 0),
        "authorization_failures": s.get("authorization_failures", 0),
        "restricted_panels": s.get("restricted_panels", 0),
        "avg_compose_ms": s.get("avg_compose_ms", 0.0),
        "by_dashboard": s.get("by_dashboard", {}),
        "capacity_models": registry.coverage()["capacity_models"],
        "resources": registry.coverage()["resources"],
        "dashboards": registry.coverage()["dashboards"],
        "panels": registry.coverage()["panels"],
    }


# --- readers for the platform Analytics registry (in-process counters; no DB, no PII) ---

def practice_dashboards_composed(principal) -> int:
    return int(stats.practice_stats().get("dashboards_composed", 0))


def practice_panels_composed(principal) -> int:
    return int(stats.practice_stats().get("panels_composed", 0))


def practice_panel_failures(principal) -> int:
    return int(stats.practice_stats().get("aggregation_failures", 0))


def practice_authorization_failures(principal) -> int:
    return int(stats.practice_stats().get("authorization_failures", 0))
