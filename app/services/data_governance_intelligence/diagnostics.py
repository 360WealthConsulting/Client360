"""Enterprise Data Governance Intelligence internal diagnostics (Phase D.66) — INTERNAL-ONLY observability of
the data-domain / lineage / stewardship / quality / retention layer. Composes the gate snapshot, in-process
counters, registry coverage (incl. configured / not_configured counts), panel availability, and the governance
report into one low-cardinality report for the ``observability.audit`` surface. Exposes NO sensitive data
values, client PII, credentials, secrets, tokens, confidential metadata, internal governance notes, or
quality-rule internals — aggregate metadata only.
"""
from __future__ import annotations

from . import gate, registry, stats
from .governance import validate_data_governance_intelligence


def _panel_availability() -> dict:
    from .panels import _COMPUTE
    return {p.key: (p.key in _COMPUTE) for p in registry.PANEL_REGISTRY}


def data_governance_diagnostics() -> dict:
    gov = validate_data_governance_intelligence()
    s = stats.data_governance_stats()
    avail = _panel_availability()
    cov = registry.coverage()
    return {
        "enabled": gate.enabled(),
        "gates": gate.gate_status(),
        "registry_coverage": cov,
        "configured_domains": cov["configured_domains"],
        "not_configured_domains": cov["not_configured_domains"],
        "not_configured_domain_keys": list(registry.not_configured_domains()),
        "data_domain_entries": cov["data_domain_entries"],
        "lineage_entries": cov["lineage_entries"],
        "stewardship_entries": cov["stewardship_entries"],
        "quality_entries": cov["quality_entries"],
        "retention_entries": cov["retention_entries"],
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
        "registered_rule_is_not_an_executed_check": True,
        "governance_coverage_not_certification": True,
        "governance": {"ok": gov["ok"], "issue_count": gov["issue_count"], "findings": gov["findings"]},
    }
