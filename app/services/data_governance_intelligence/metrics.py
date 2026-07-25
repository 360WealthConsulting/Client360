"""Enterprise Data Governance Intelligence analytics readers (Phase D.66).

These are LOW-CARDINALITY operational counters ABOUT the data-governance layer itself (how many dashboards /
panels / summaries were composed, failures) — registered into the SINGLE, existing Analytics Registry. This
layer defines NO data-domain / lineage / stewardship / quality / retention business metrics (those stay owned
by their authoritative owners) and NO second metrics registry. Never sensitive data values, client PII,
credentials, secrets, tokens, confidential metadata, or quality-rule internals.
"""
from __future__ import annotations

from . import registry, stats


def data_governance_intelligence_metrics(principal=None) -> dict:
    s = stats.data_governance_stats()
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
        "data_domain_entries": cov["data_domain_entries"],
        "lineage_entries": cov["lineage_entries"],
        "stewardship_entries": cov["stewardship_entries"],
        "quality_entries": cov["quality_entries"],
        "retention_entries": cov["retention_entries"],
        "dashboards": cov["dashboards"],
        "panels": cov["panels"],
    }


# --- readers for the platform Analytics registry (in-process counters; no DB, no PII) ---

def data_governance_dashboards_composed(principal) -> int:
    return int(stats.data_governance_stats().get("dashboards_composed", 0))


def data_governance_panels_composed(principal) -> int:
    return int(stats.data_governance_stats().get("panels_composed", 0))


def data_governance_panel_failures(principal) -> int:
    return int(stats.data_governance_stats().get("aggregation_failures", 0))


def data_governance_authorization_failures(principal) -> int:
    return int(stats.data_governance_stats().get("authorization_failures", 0))
