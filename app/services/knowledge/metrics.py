"""Knowledge analytics (Phase D.45) — LOW-CARDINALITY aggregates only. Never client identifiers,
relationship contents, or evidence. These feed the platform Analytics registry + internal diagnostics.
"""
from __future__ import annotations

from . import registry, stats


def knowledge_metrics(principal=None) -> dict:
    s = stats.knowledge_stats()
    return {
        "graphs_composed": s.get("graphs_composed", 0),
        "traversals": s.get("traversals", 0),
        "searches": s.get("searches", 0),
        "explanations": s.get("explanations", 0),
        "adapter_failures": s.get("adapter_failures", 0),
        "hidden_suppressed": s.get("hidden_suppressed", 0),
        "avg_traverse_ms": s.get("avg_traverse_ms", 0.0),
        "relationship_type_distribution": s.get("by_edge_type", {}),
        "registry_entity_types": registry.coverage()["entity_types"],
        "registry_relationship_types": registry.coverage()["relationship_types"],
    }


# --- readers for the platform Analytics registry (in-process counters; no DB, no PII) ---

def knowledge_traversal_count(principal) -> int:
    return int(stats.knowledge_stats().get("traversals", 0))


def knowledge_explanation_count(principal) -> int:
    return int(stats.knowledge_stats().get("explanations", 0))


def knowledge_search_count(principal) -> int:
    return int(stats.knowledge_stats().get("searches", 0))


def knowledge_adapter_failure_count(principal) -> int:
    return int(stats.knowledge_stats().get("adapter_failures", 0))
