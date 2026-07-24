"""Compliance Intelligence internal diagnostics (Phase D.47) — INTERNAL-ONLY observability of the supervisory
layer. Composes the gate snapshot, in-process counters, registry coverage, adapter availability, and the
governance report into one low-cardinality report for the ``observability.audit`` surface. Exposes NO
supervisory evidence, client identifiers, reviewer names, or sensitive content — aggregates only.
"""
from __future__ import annotations

from . import gate, registry, stats
from .governance import validate_compliance_intelligence


def _adapter_availability() -> dict:
    avail = {}
    for name in ("reviews", "exceptions", "licensing"):
        try:
            __import__(f"app.services.compliance_intelligence.adapters.{name}")
            avail[name] = True
        except Exception:
            avail[name] = False
    return avail


def compliance_diagnostics() -> dict:
    gov = validate_compliance_intelligence()
    s = stats.compliance_stats()
    return {
        "enabled": gate.enabled(),
        "gates": gate.gate_status(),
        "registry_coverage": registry.coverage(),
        "adapter_availability": _adapter_availability(),
        "stats": s,
        "reviews_composed": s.get("reviews_composed", 0),
        "exceptions_composed": s.get("exceptions_composed", 0),
        "overdue_reviews": s.get("overdue_reviews", 0),
        "suppressed": s.get("suppressed", 0),
        "missing_evidence": s.get("missing_evidence", 0),
        "authorization_failures": s.get("authorization_failures", 0),
        "adapter_failures_by_source": s.get("by_source_failure", {}),
        "by_review_type": s.get("by_review_type", {}),
        "by_severity": s.get("by_severity", {}),
        "avg_compose_ms": s.get("avg_compose_ms", 0.0),
        "governance": {"ok": gov["ok"], "issue_count": gov["issue_count"], "findings": gov["findings"]},
    }
