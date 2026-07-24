"""In-process engagement counters (Phase D.44).

Low-cardinality aggregates ONLY — never person/household ids, participant names, subjects, message/email
bodies, or previews. These back the low-cardinality analytics metrics + internal diagnostics.
"""
from __future__ import annotations

import threading

_lock = threading.RLock()
_STATS = {
    "timeline_composed": 0, "searches": 0, "portal_composed": 0, "summaries": 0,
    "adapter_failures": 0, "suppressed_internal": 0, "duplicates_collapsed": 0,
    "compose_ms_total": 0.0, "by_type": {}, "by_source_failure": {},
}


def note(kind: str, *, interaction_type: str | None = None, source: str | None = None, amount=1):
    with _lock:
        _STATS[kind] = _STATS.get(kind, 0) + amount
        if interaction_type:
            _STATS["by_type"][interaction_type] = _STATS["by_type"].get(interaction_type, 0) + 1
        if source:
            _STATS["by_source_failure"][source] = _STATS["by_source_failure"].get(source, 0) + 1


def note_ms(ms: float):
    with _lock:
        _STATS["compose_ms_total"] += ms


def engagement_stats() -> dict:
    with _lock:
        s = dict(_STATS)
        s["by_type"] = dict(_STATS["by_type"])
        s["by_source_failure"] = dict(_STATS["by_source_failure"])
    composed = s["timeline_composed"] + s["portal_composed"]
    s["avg_compose_ms"] = round(s["compose_ms_total"] / composed, 1) if composed else 0.0
    del s["compose_ms_total"]
    return s


def reset_stats():
    with _lock:
        for k, v in list(_STATS.items()):
            _STATS[k] = {} if isinstance(v, dict) else (0.0 if isinstance(v, float) else 0)
