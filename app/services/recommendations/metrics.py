"""Operational Intelligence analytics (Phase D.46) — LOW-CARDINALITY aggregates only. Never client
identifiers or recommendation evidence. These feed the platform Analytics registry + internal diagnostics.
"""
from __future__ import annotations

from . import registry, stats


def recommendation_metrics(principal=None) -> dict:
    s = stats.recommendation_stats()
    return {
        "generated": s.get("generated", 0),
        "suppressed": s.get("suppressed", 0),
        "compositions": s.get("compositions", 0),
        "missing_evidence": s.get("missing_evidence", 0),
        "adapter_failures": s.get("adapter_failures", 0),
        "duplicates_collapsed": s.get("duplicates_collapsed", 0),
        "avg_compose_ms": s.get("avg_compose_ms", 0.0),
        "by_category": s.get("by_category", {}),
        "by_severity": s.get("by_severity", {}),
        "registry_types": registry.coverage()["total_types"],
    }


# --- readers for the platform Analytics registry (in-process counters; no DB, no PII) ---

def recommendations_generated(principal) -> int:
    return int(stats.recommendation_stats().get("generated", 0))


def recommendations_suppressed(principal) -> int:
    return int(stats.recommendation_stats().get("suppressed", 0))


def recommendation_compositions(principal) -> int:
    return int(stats.recommendation_stats().get("compositions", 0))


def recommendation_adapter_failures(principal) -> int:
    return int(stats.recommendation_stats().get("adapter_failures", 0))
