"""Enterprise Identity & Access Governance analytics readers (Phase D.65).

These are LOW-CARDINALITY operational counters ABOUT the identity layer itself (how many dashboards / panels /
summaries were composed, failures) — registered into the SINGLE, existing Analytics Registry. This layer
defines NO identity / role / capability / authentication / authorization business metrics (those stay owned by
their authoritative owners) and NO second metrics registry. Never passwords, secrets, tokens, session IDs,
credentials, authentication payloads, raw identities, or user-level permission maps.
"""
from __future__ import annotations

from . import registry, stats


def identity_governance_metrics(principal=None) -> dict:
    s = stats.identity_stats()
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
        "identity_domains": cov["identity_domains"],
        "role_domains": cov["role_domains"],
        "capability_domains": cov["capability_domains"],
        "authentication_domains": cov["authentication_domains"],
        "authorization_domains": cov["authorization_domains"],
        "dashboards": cov["dashboards"],
        "panels": cov["panels"],
    }


# --- readers for the platform Analytics registry (in-process counters; no DB, no PII) ---

def identity_dashboards_composed(principal) -> int:
    return int(stats.identity_stats().get("dashboards_composed", 0))


def identity_panels_composed(principal) -> int:
    return int(stats.identity_stats().get("panels_composed", 0))


def identity_panel_failures(principal) -> int:
    return int(stats.identity_stats().get("aggregation_failures", 0))


def identity_authorization_failures(principal) -> int:
    return int(stats.identity_stats().get("authorization_failures", 0))
