"""Engagement internal diagnostics (Phase D.44) — INTERNAL-ONLY observability of the unified
communications layer. Composes the gate snapshot, in-process counters, registry coverage, adapter
availability, and the governance report into one low-cardinality report for the internal
``observability.audit`` surface. Exposes NO client data, NO identifiers, NO previews.
"""
from __future__ import annotations

from . import gate, registry, stats
from .governance import validate_engagement


def _adapter_availability() -> dict:
    """Best-effort import check for each adapter module — availability, never data."""
    avail = {}
    for name in ("timeline", "portal"):
        try:
            __import__(f"app.services.communications.engagement.adapters.{name}")
            avail[name] = True
        except Exception:
            avail[name] = False
    return avail


def engagement_diagnostics() -> dict:
    gov = validate_engagement()
    s = stats.engagement_stats()
    return {
        "enabled": gate.enabled(),
        "gates": gate.gate_status(),
        "registry_coverage": registry.coverage(),
        "adapter_availability": _adapter_availability(),
        "stats": s,
        "interaction_counts_by_type": s.get("by_type", {}),
        "adapter_failures_by_source": s.get("by_source_failure", {}),
        "duplicates_collapsed": s.get("duplicates_collapsed", 0),
        "suppressed_internal": s.get("suppressed_internal", 0),
        "avg_compose_ms": s.get("avg_compose_ms", 0.0),
        "governance": {"ok": gov["ok"], "issue_count": gov["issue_count"], "findings": gov["findings"]},
    }
