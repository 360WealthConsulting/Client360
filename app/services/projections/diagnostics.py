"""Projection diagnostics (Phase D.36) — read-only inspection of a read model.

Reports per-projection health, lag, last processed event, processing rate, rebuild/replay duration,
size (row count), events processed, failed events, and the rebuild history. Read-only — it never
processes events or mutates production state.
"""
from __future__ import annotations

from . import engine, registry


def diagnostics(projection_id) -> dict:
    """The full diagnostic view of a projection."""
    d = registry.get_definition(projection_id)
    if d is None:
        return {}
    st = engine.state(projection_id)
    lag = engine.lag(projection_id)
    size = engine.size(projection_id)
    processed = st["events_processed"]
    rate = None
    if st.get("last_rebuild_duration_ms"):
        rate = round(processed / (st["last_rebuild_duration_ms"] / 1000.0), 2) if st["last_rebuild_duration_ms"] else None
    return {"projection_id": projection_id, "name": d["name"], "owner": d["owner"],
            "read_table": d["read_table"], "subscribed_events": d["subscribed_events"],
            "schema_version": d["schema_version"], "status": d["status"],
            "health": st["health"], "lag": lag, "size": size,
            "last_processed_event_id": st["last_processed_event_id"],
            "last_processed_at": st["last_processed_at"], "events_processed": processed,
            "failed_events": st["failed_events"], "processing_rate_eps": rate,
            "rebuild_count": st["rebuild_count"], "replay_count": st["replay_count"],
            "last_rebuild_duration_ms": st["last_rebuild_duration_ms"],
            "last_replay_duration_ms": st["last_replay_duration_ms"],
            "last_validation_ok": st.get("last_validation_ok"),
            "rebuild_history": st.get("rebuild_history") or [], "last_error": st.get("last_error")}


def health() -> dict:
    """A fleet health summary across all projections."""
    out = []
    for d in registry.list_definitions():
        st = engine.state(d["projection_id"])
        out.append({"projection_id": d["projection_id"], "health": st["health"],
                    "lag": engine.lag(d["projection_id"]), "size": engine.size(d["projection_id"]),
                    "events_processed": st["events_processed"], "failed_events": st["failed_events"]})
    counts = {}
    for p in out:
        counts[p["health"]] = counts.get(p["health"], 0) + 1
    return {"projections": out, "by_health": counts,
            "healthy": counts.get("healthy", 0), "lagging": counts.get("lagging", 0),
            "failed": counts.get("failed", 0), "unbuilt": counts.get("unbuilt", 0)}


def largest_projection() -> dict:
    sizes = [{"projection_id": d["projection_id"], "size": engine.size(d["projection_id"])}
             for d in registry.list_definitions()]
    return max(sizes, key=lambda s: s["size"], default={"projection_id": None, "size": 0})
