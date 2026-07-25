"""Enterprise Change Management internal diagnostics (Phase D.63) — INTERNAL-ONLY observability of the change /
release / configuration layer. Composes the gate snapshot, in-process counters, registry coverage (incl.
configured / not_configured counts), panel availability, and the governance report into one low-cardinality
report for the ``observability.audit`` surface. Exposes NO credentials, secrets, tokens, environment variables,
connection strings, private keys, deployment payloads, protected infrastructure details, sensitive
configuration values, private incident narratives, or repository credentials — aggregate metadata only.
"""
from __future__ import annotations

from . import gate, registry, stats
from .governance import validate_change_management


def _panel_availability() -> dict:
    from .panels import _COMPUTE
    return {p.key: (p.key in _COMPUTE) for p in registry.PANEL_REGISTRY}


def change_diagnostics() -> dict:
    gov = validate_change_management()
    s = stats.change_stats()
    avail = _panel_availability()
    cov = registry.coverage()
    return {
        "enabled": gate.enabled(),
        "gates": gate.gate_status(),
        "registry_coverage": cov,
        "configured_domains": cov["configured_domains"],
        "not_configured_domains": cov["not_configured_domains"],
        "not_configured_domain_keys": list(registry.not_configured_domains()),
        "change_domains": cov["change_domains"],
        "release_entries": cov["release_entries"],
        "configuration_entries": cov["configuration_entries"],
        "change_evidence_entries": cov["change_evidence"],
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
        "green_ci_is_not_production": True, "merged_is_not_deployed": True,
        "governance": {"ok": gov["ok"], "issue_count": gov["issue_count"], "findings": gov["findings"]},
    }
