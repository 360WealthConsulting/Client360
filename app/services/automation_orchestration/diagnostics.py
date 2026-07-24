"""Automation Orchestration internal diagnostics (Phase D.51) — INTERNAL-ONLY observability of the
automation-orchestration layer. Composes the gate snapshot, in-process counters, registry coverage, panel
availability, and the governance report into one low-cardinality report for the ``observability.audit``
surface. Exposes NO workflow payloads, entity identifiers, or client-sensitive automation data — aggregates
only.
"""
from __future__ import annotations

from . import gate, registry, stats
from .governance import validate_automation_orchestration


def _panel_availability() -> dict:
    """Best-effort check that every registered panel has a compute function."""
    from .panels import _COMPUTE
    return {p.key: (p.key in _COMPUTE) for p in registry.PANEL_REGISTRY}


def automation_diagnostics() -> dict:
    gov = validate_automation_orchestration()
    s = stats.automation_stats()
    avail = _panel_availability()
    return {
        "enabled": gate.enabled(),
        "gates": gate.gate_status(),
        "registry_coverage": registry.coverage(),
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
        "governance": {"ok": gov["ok"], "issue_count": gov["issue_count"], "findings": gov["findings"]},
    }
