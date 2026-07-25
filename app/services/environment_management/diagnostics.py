"""Enterprise Environment Management internal diagnostics (Phase D.64) — INTERNAL-ONLY observability of the
environment / platform / deployment-topology / lifecycle / dependency layer. Composes the gate snapshot,
in-process counters, registry coverage (incl. configured / not_configured counts), panel availability, and the
governance report into one low-cardinality report for the ``observability.audit`` surface. Exposes NO
credentials, secrets, tokens, environment variables, connection strings, private keys, deployment payloads,
protected infrastructure details, private topology, or sensitive configuration values — aggregate metadata
only.
"""
from __future__ import annotations

from . import gate, registry, stats
from .governance import validate_environment_management


def _panel_availability() -> dict:
    from .panels import _COMPUTE
    return {p.key: (p.key in _COMPUTE) for p in registry.PANEL_REGISTRY}


def environment_diagnostics() -> dict:
    gov = validate_environment_management()
    s = stats.environment_stats()
    avail = _panel_availability()
    cov = registry.coverage()
    return {
        "enabled": gate.enabled(),
        "gates": gate.gate_status(),
        "registry_coverage": cov,
        "configured_domains": cov["configured_domains"],
        "not_configured_domains": cov["not_configured_domains"],
        "not_configured_domain_keys": list(registry.not_configured_domains()),
        "environment_domains": cov["environment_domains"],
        "platform_domains": cov["platform_domains"],
        "deployment_topology_domains": cov["deployment_topology_domains"],
        "lifecycle_domains": cov["lifecycle_domains"],
        "infrastructure_dependency_domains": cov["infrastructure_dependency_domains"],
        "panel_compute_coverage": {"total": len(avail), "with_compute": sum(1 for v in avail.values() if v)},
        "stats": s,
        "dashboards_composed": s.get("dashboards_composed", 0),
        "panels_composed": s.get("panels_composed", 0),
        "summaries_composed": s.get("summaries_composed", 0),
        "aggregation_failures": s.get("aggregation_failures", 0),
        "authorization_failures": s.get("authorization_failures", 0),
        "restricted_panels": s.get("restricted_panels", 0),
        "panel_failures_by_key": s.get("by_panel_failure", {}),
        "by_dashboard": s.get("by_dashboard", {}),
        "avg_compose_ms": s.get("avg_compose_ms", 0.0),
        "environment_metadata_is_not_live_infrastructure": True,
        "deployment_reference_is_not_a_deployment": True,
        "governance": {"ok": gov["ok"], "issue_count": gov["issue_count"], "findings": gov["findings"]},
    }
