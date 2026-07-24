"""Engagement analytics (Phase D.44) — LOW-CARDINALITY aggregates only.

Never emits client identifiers, participant names, subjects, or previews. Composition volume + adapter
health come from the in-process counters; the scoped communication-volume figure reuses the authoritative
``communications.service.metrics`` (already record-scoped). These feed the platform Analytics registry and
the internal diagnostics.
"""
from __future__ import annotations

from . import registry, stats


def engagement_metrics(principal=None) -> dict:
    """Firm-level, low-cardinality engagement aggregates. Safe to expose to an analytics/diagnostics
    surface — no per-client data."""
    s = stats.engagement_stats()
    out = {
        "timeline_composed": s.get("timeline_composed", 0),
        "portal_composed": s.get("portal_composed", 0),
        "searches": s.get("searches", 0),
        "summaries": s.get("summaries", 0),
        "adapter_failures": s.get("adapter_failures", 0),
        "suppressed_internal": s.get("suppressed_internal", 0),
        "avg_compose_ms": s.get("avg_compose_ms", 0.0),
        "interaction_type_distribution": s.get("by_type", {}),
        "registry_types": registry.coverage()["total_types"],
    }
    # Scoped communication volume reuses the authoritative, record-scoped communications metrics.
    if principal is not None:
        try:
            from app.services.communications.service import metrics as comms_metrics
            cm = comms_metrics(principal)
            out["open_conversations"] = cm.get("open_conversations")
            out["messages"] = cm.get("messages")
        except Exception:
            pass
    return out


# --- readers for the platform Analytics registry (in-process counters; no DB, no PII) ---

def interactions_composed_count(principal) -> int:
    s = stats.engagement_stats()
    return int(s.get("timeline_composed", 0)) + int(s.get("portal_composed", 0))


def engagement_search_count(principal) -> int:
    return int(stats.engagement_stats().get("searches", 0))


def engagement_adapter_failure_count(principal) -> int:
    return int(stats.engagement_stats().get("adapter_failures", 0))
