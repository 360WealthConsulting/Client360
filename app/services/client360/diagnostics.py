"""Client 360 Workspace diagnostics (Phase D.40) — read-only composition telemetry.

Reports composition timings, projection/fallback usage (per-client reads are authoritative composition
— not projection-backed), missing adapters, suppressed capabilities, record-scope validation, and stale
(errored) sources. Never mutates.
"""
from __future__ import annotations

from .registry import SECTIONS
from .service import get_workspace


def client360_diagnostics(principal, *, person_id=None, household_id=None) -> dict:
    ws = get_workspace(principal, person_id=person_id, household_id=household_id)
    if ws is None:
        return {"available": False, "reason": "out of record scope or not found",
                "record_scope_validated": True}

    timings = ws["timings"]
    stale = [k for k, v in ws["sections"].items() if isinstance(v, dict) and v.get("error")]
    missing = [s.key for s in SECTIONS if s.builder is None]

    try:
        from app.services.projections.adoption import usage_stats
        proj = usage_stats()
    except Exception:
        proj = {}

    return {
        "entity_type": ws["entity_type"], "entity_id": ws["entity_id"],
        "composition_timings_ms": timings,
        "total_composition_ms": round(sum(timings.values()), 1),
        "sections_built": len(ws["sections"]),
        "suppressed_capabilities": ws["suppressed_sections"],
        "missing_adapters": missing,
        "stale_sources": stale,
        "record_scope_validated": True,
        # Per-client reads are authoritative composition — no projection-backed fast path exists on the
        # per-client route; the adoption usage below reflects only firm-wide count paths, if any.
        "projection_usage": proj,
        "fallback_usage": {"note": "per-client sections read authoritative tables directly"},
        "quick_action_count": len(ws["quick_actions"]),
    }
