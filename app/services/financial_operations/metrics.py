"""Financial Operations analytics readers (Phase D.57).

These are LOW-CARDINALITY operational counters ABOUT the financial-operations layer itself (how many
dashboards/panels/summaries were composed, failures) — registered into the SINGLE, existing Analytics
Registry. This layer defines NO business metrics and NO second metrics registry; every financial figure comes
from the authoritative financial owners (the insurance commission ledger, the portfolio AUM owner, the single
Analytics Registry revenue metrics). Never payroll details, tax returns, bank account numbers, payment
credentials, or accounting payloads.
"""
from __future__ import annotations

from . import registry, stats


def financial_operations_metrics(principal=None) -> dict:
    s = stats.financial_stats()
    return {
        "dashboards_composed": s.get("dashboards_composed", 0),
        "panels_composed": s.get("panels_composed", 0),
        "summaries_composed": s.get("summaries_composed", 0),
        "aggregation_failures": s.get("aggregation_failures", 0),
        "authorization_failures": s.get("authorization_failures", 0),
        "restricted_panels": s.get("restricted_panels", 0),
        "avg_compose_ms": s.get("avg_compose_ms", 0.0),
        "by_dashboard": s.get("by_dashboard", {}),
        "financial_categories": registry.coverage()["financial_categories"],
        "revenue_types": registry.coverage()["revenue_types"],
        "dashboards": registry.coverage()["dashboards"],
        "panels": registry.coverage()["panels"],
    }


# --- readers for the platform Analytics registry (in-process counters; no DB, no PII) ---

def financial_dashboards_composed(principal) -> int:
    return int(stats.financial_stats().get("dashboards_composed", 0))


def financial_panels_composed(principal) -> int:
    return int(stats.financial_stats().get("panels_composed", 0))


def financial_panel_failures(principal) -> int:
    return int(stats.financial_stats().get("aggregation_failures", 0))


def financial_authorization_failures(principal) -> int:
    return int(stats.financial_stats().get("authorization_failures", 0))
