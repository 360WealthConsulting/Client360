"""Enterprise Risk Management analytics readers (Phase D.58).

These are LOW-CARDINALITY operational counters ABOUT the risk-management layer itself (how many
dashboards/panels/summaries were composed, failures) — registered into the SINGLE, existing Analytics
Registry. This layer defines NO business-risk metrics (those stay owned by Compliance Intelligence, the
Exception Engine, Security, etc.) and NO second metrics registry. Never client-sensitive evidence, audit
payloads, security details, credentials, tokens, bank information, tax-return contents, document contents, or
private incident narratives.
"""
from __future__ import annotations

from . import registry, stats


def enterprise_risk_metrics(principal=None) -> dict:
    s = stats.risk_stats()
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
        "risk_domains": cov["risk_domains"],
        "control_families": cov["control_families"],
        "assurance_sources": cov["assurance_sources"],
        "dashboards": cov["dashboards"],
        "panels": cov["panels"],
    }


# --- readers for the platform Analytics registry (in-process counters; no DB, no PII) ---

def risk_dashboards_composed(principal) -> int:
    return int(stats.risk_stats().get("dashboards_composed", 0))


def risk_panels_composed(principal) -> int:
    return int(stats.risk_stats().get("panels_composed", 0))


def risk_panel_failures(principal) -> int:
    return int(stats.risk_stats().get("aggregation_failures", 0))


def risk_authorization_failures(principal) -> int:
    return int(stats.risk_stats().get("authorization_failures", 0))
