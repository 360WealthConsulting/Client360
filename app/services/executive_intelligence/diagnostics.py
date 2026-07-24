"""Executive Reporting internal diagnostics (Phase D.48) — INTERNAL-ONLY observability of the
executive-intelligence layer. Composes the gate snapshot, in-process counters, registry coverage, widget
availability, and the governance report into one low-cardinality report for the ``observability.audit``
surface. Exposes NO metric values, client identifiers, or business data — aggregates only.
"""
from __future__ import annotations

from . import gate, registry, stats
from .governance import validate_executive_reporting


def _widget_availability() -> dict:
    """Best-effort check that every registered widget has a compute function."""
    from .widgets import _COMPUTE
    return {w.key: (w.key in _COMPUTE) for w in registry.WIDGET_REGISTRY}


def reporting_diagnostics() -> dict:
    gov = validate_executive_reporting()
    s = stats.reporting_stats()
    avail = _widget_availability()
    return {
        "enabled": gate.enabled(),
        "gates": gate.gate_status(),
        "registry_coverage": registry.coverage(),
        "widget_compute_coverage": {"total": len(avail), "with_compute": sum(1 for v in avail.values() if v)},
        "stats": s,
        "dashboards_composed": s.get("dashboards_composed", 0),
        "widgets_composed": s.get("widgets_composed", 0),
        "aggregation_failures": s.get("aggregation_failures", 0),
        "authorization_failures": s.get("authorization_failures", 0),
        "restricted_widgets": s.get("restricted_widgets", 0),
        "widget_failures_by_key": s.get("by_widget_failure", {}),
        "by_dashboard": s.get("by_dashboard", {}),
        "avg_compose_ms": s.get("avg_compose_ms", 0.0),
        "governance": {"ok": gov["ok"], "issue_count": gov["issue_count"], "findings": gov["findings"]},
    }
