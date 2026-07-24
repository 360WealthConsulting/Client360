"""Integration Hub analytics readers (Phase D.53).

These are LOW-CARDINALITY operational counters ABOUT the integration-hub layer itself (how many
dashboards/panels/summaries were composed, failures) — registered into the SINGLE, existing Analytics
Registry. This layer defines NO business metrics and NO second metrics registry; every integration count
comes from the authoritative Integration Platform. Never secrets, tokens, credentials, or client payloads.
"""
from __future__ import annotations

from . import registry, stats


def integration_hub_metrics(principal=None) -> dict:
    s = stats.integration_stats()
    return {
        "dashboards_composed": s.get("dashboards_composed", 0),
        "panels_composed": s.get("panels_composed", 0),
        "summaries_composed": s.get("summaries_composed", 0),
        "aggregation_failures": s.get("aggregation_failures", 0),
        "authorization_failures": s.get("authorization_failures", 0),
        "restricted_panels": s.get("restricted_panels", 0),
        "avg_compose_ms": s.get("avg_compose_ms", 0.0),
        "by_dashboard": s.get("by_dashboard", {}),
        "integrations": registry.coverage()["integrations"],
        "connectors": registry.coverage()["connectors"],
        "dashboards": registry.coverage()["dashboards"],
        "panels": registry.coverage()["panels"],
    }


# --- readers for the platform Analytics registry (in-process counters; no DB, no PII) ---

def integration_dashboards_composed(principal) -> int:
    return int(stats.integration_stats().get("dashboards_composed", 0))


def integration_panels_composed(principal) -> int:
    return int(stats.integration_stats().get("panels_composed", 0))


def integration_panel_failures(principal) -> int:
    return int(stats.integration_stats().get("aggregation_failures", 0))


def integration_authorization_failures(principal) -> int:
    return int(stats.integration_stats().get("authorization_failures", 0))
