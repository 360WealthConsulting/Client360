"""Knowledge graph internal diagnostics (Phase D.45) — INTERNAL-ONLY observability of the semantic layer.
Composes the gate snapshot, in-process counters, registry coverage, adapter availability, and the
governance report into one low-cardinality report for the ``observability.audit`` surface. Exposes NO
client relationship contents, NO identifiers, NO evidence text — aggregates only.
"""
from __future__ import annotations

from . import gate, registry, stats
from .governance import validate_knowledge


def _adapter_availability() -> dict:
    avail = {}
    for name in ("relationship", "advisor", "domain"):
        try:
            __import__(f"app.services.knowledge.adapters.{name}")
            avail[name] = True
        except Exception:
            avail[name] = False
    return avail


def knowledge_diagnostics() -> dict:
    gov = validate_knowledge()
    s = stats.knowledge_stats()
    return {
        "enabled": gate.enabled(),
        "gates": gate.gate_status(),
        "registry_coverage": registry.coverage(),
        "adapter_availability": _adapter_availability(),
        "stats": s,
        "relationship_counts_by_type": s.get("by_edge_type", {}),
        "adapter_failures_by_source": s.get("by_source_failure", {}),
        "traversal_depth_distribution": s.get("by_depth", {}),
        "hidden_suppressed": s.get("hidden_suppressed", 0),
        "orphan_relationships": s.get("orphan_relationships", 0),
        "cycles_avoided": s.get("cycles_avoided", 0),
        "avg_traverse_ms": s.get("avg_traverse_ms", 0.0),
        "governance": {"ok": gov["ok"], "issue_count": gov["issue_count"], "findings": gov["findings"]},
    }
