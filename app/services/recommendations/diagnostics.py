"""Operational Intelligence internal diagnostics (Phase D.46) — INTERNAL-ONLY observability of the
recommendation layer. Composes the gate snapshot, in-process counters, registry coverage, adapter
availability, and the governance report into one low-cardinality report for the ``observability.audit``
surface. Exposes NO recommendation evidence, client identifiers, or sensitive content — aggregates only.
"""
from __future__ import annotations

from . import gate, registry, stats
from .governance import validate_recommendations


def _adapter_availability() -> dict:
    avail = {}
    for name in ("signals", "observations", "composed"):
        try:
            __import__(f"app.services.recommendations.adapters.{name}")
            avail[name] = True
        except Exception:
            avail[name] = False
    return avail


def recommendation_diagnostics() -> dict:
    gov = validate_recommendations()
    s = stats.recommendation_stats()
    return {
        "enabled": gate.enabled(),
        "gates": gate.gate_status(),
        "registry_coverage": registry.coverage(),
        "adapter_availability": _adapter_availability(),
        "stats": s,
        "recommendations_generated": s.get("generated", 0),
        "suppressed": s.get("suppressed", 0),
        "stale": s.get("stale", 0),
        "missing_evidence": s.get("missing_evidence", 0),
        "rule_failures": s.get("rule_failures", 0),
        "adapter_failures_by_source": s.get("by_source_failure", {}),
        "by_category": s.get("by_category", {}),
        "by_severity": s.get("by_severity", {}),
        "avg_compose_ms": s.get("avg_compose_ms", 0.0),
        "governance": {"ok": gov["ok"], "issue_count": gov["issue_count"], "findings": gov["findings"]},
    }
