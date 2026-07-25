"""Enterprise Environment Management analytics readers (Phase D.64).

These are LOW-CARDINALITY operational counters ABOUT the environment layer itself (how many dashboards / panels
/ summaries were composed, failures) — registered into the SINGLE, existing Analytics Registry. This layer
defines NO environment / platform / deployment / infrastructure business metrics (those stay owned by their
authoritative owners) and NO second metrics registry. Never credentials, secrets, tokens, environment
variables, connection strings, private keys, deployment payloads, private topology, or sensitive configuration
values.
"""
from __future__ import annotations

from . import registry, stats


def environment_management_metrics(principal=None) -> dict:
    s = stats.environment_stats()
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
        "environment_domains": cov["environment_domains"],
        "platform_domains": cov["platform_domains"],
        "deployment_topology_domains": cov["deployment_topology_domains"],
        "lifecycle_domains": cov["lifecycle_domains"],
        "infrastructure_dependency_domains": cov["infrastructure_dependency_domains"],
        "dashboards": cov["dashboards"],
        "panels": cov["panels"],
    }


# --- readers for the platform Analytics registry (in-process counters; no DB, no PII) ---

def environment_dashboards_composed(principal) -> int:
    return int(stats.environment_stats().get("dashboards_composed", 0))


def environment_panels_composed(principal) -> int:
    return int(stats.environment_stats().get("panels_composed", 0))


def environment_panel_failures(principal) -> int:
    return int(stats.environment_stats().get("aggregation_failures", 0))


def environment_authorization_failures(principal) -> int:
    return int(stats.environment_stats().get("authorization_failures", 0))
