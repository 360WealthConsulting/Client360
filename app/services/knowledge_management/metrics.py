"""Enterprise Knowledge Management analytics readers (Phase D.62).

These are LOW-CARDINALITY operational counters ABOUT the knowledge layer itself (how many
dashboards/panels/summaries were composed, failures) — registered into the SINGLE, existing Analytics
Registry. This layer defines NO knowledge / SOP / documentation business metrics (those stay owned by their
authoritative owners) and NO second metrics registry. Never document contents, confidential procedures,
credentials, tokens, or client-sensitive documentation.
"""
from __future__ import annotations

from . import registry, stats


def knowledge_management_metrics(principal=None) -> dict:
    s = stats.knowledge_stats()
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
        "knowledge_domains": cov["knowledge_domains"],
        "sop_categories": cov["sop_categories"],
        "knowledge_sources": cov["knowledge_sources"],
        "dashboards": cov["dashboards"],
        "panels": cov["panels"],
    }


# --- readers for the platform Analytics registry (in-process counters; no DB, no PII) ---

def knowledge_dashboards_composed(principal) -> int:
    return int(stats.knowledge_stats().get("dashboards_composed", 0))


def knowledge_panels_composed(principal) -> int:
    return int(stats.knowledge_stats().get("panels_composed", 0))


def knowledge_panel_failures(principal) -> int:
    return int(stats.knowledge_stats().get("aggregation_failures", 0))


def knowledge_authorization_failures(principal) -> int:
    return int(stats.knowledge_stats().get("authorization_failures", 0))
