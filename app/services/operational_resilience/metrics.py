"""Enterprise Operational Resilience analytics readers (Phase D.60).

These are LOW-CARDINALITY operational counters ABOUT the resilience layer itself (how many
dashboards/panels/summaries were composed, failures) — registered into the SINGLE, existing Analytics
Registry. This layer defines NO incident / monitoring / continuity business metrics (those stay owned by their
authoritative owners) and NO second metrics registry. Never a sensitive operational payload.
"""
from __future__ import annotations

from . import registry, stats


def operational_resilience_metrics(principal=None) -> dict:
    s = stats.resilience_stats()
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
        "operational_services": cov["operational_services"],
        "incident_categories": cov["incident_categories"],
        "continuity_capabilities": cov["continuity_capabilities"],
        "recovery_objectives": cov["recovery_objectives"],
        "operational_dependencies": cov["operational_dependencies"],
        "dashboards": cov["dashboards"],
        "panels": cov["panels"],
    }


# --- readers for the platform Analytics registry (in-process counters; no DB, no PII) ---

def resilience_dashboards_composed(principal) -> int:
    return int(stats.resilience_stats().get("dashboards_composed", 0))


def resilience_panels_composed(principal) -> int:
    return int(stats.resilience_stats().get("panels_composed", 0))


def resilience_panel_failures(principal) -> int:
    return int(stats.resilience_stats().get("aggregation_failures", 0))


def resilience_authorization_failures(principal) -> int:
    return int(stats.resilience_stats().get("authorization_failures", 0))
