"""Vendor Management analytics readers (Phase D.56).

These are LOW-CARDINALITY operational counters ABOUT the vendor-management layer itself (how many
dashboards/panels/summaries were composed, failures) — registered into the SINGLE, existing Analytics
Registry. This layer defines NO business metrics and NO second metrics registry; every vendor count comes
from the authoritative vendor / technology owners. Never contract contents, credentials, license keys,
secrets, or procurement payloads.
"""
from __future__ import annotations

from . import registry, stats


def vendor_management_metrics(principal=None) -> dict:
    s = stats.vendor_stats()
    return {
        "dashboards_composed": s.get("dashboards_composed", 0),
        "panels_composed": s.get("panels_composed", 0),
        "summaries_composed": s.get("summaries_composed", 0),
        "aggregation_failures": s.get("aggregation_failures", 0),
        "authorization_failures": s.get("authorization_failures", 0),
        "restricted_panels": s.get("restricted_panels", 0),
        "avg_compose_ms": s.get("avg_compose_ms", 0.0),
        "by_dashboard": s.get("by_dashboard", {}),
        "vendor_classes": registry.coverage()["vendor_classes"],
        "lifecycle_classes": registry.coverage()["lifecycle_classes"],
        "dashboards": registry.coverage()["dashboards"],
        "panels": registry.coverage()["panels"],
    }


# --- readers for the platform Analytics registry (in-process counters; no DB, no PII) ---

def vendor_dashboards_composed(principal) -> int:
    return int(stats.vendor_stats().get("dashboards_composed", 0))


def vendor_panels_composed(principal) -> int:
    return int(stats.vendor_stats().get("panels_composed", 0))


def vendor_panel_failures(principal) -> int:
    return int(stats.vendor_stats().get("aggregation_failures", 0))


def vendor_authorization_failures(principal) -> int:
    return int(stats.vendor_stats().get("authorization_failures", 0))
