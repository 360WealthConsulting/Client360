"""In-process knowledge graph counters (Phase D.45).

Low-cardinality aggregates ONLY — never person/household/entity ids, names, relationship contents, or
evidence text. These back the low-cardinality analytics metrics + internal diagnostics + observability
instrumentation (the platform's established in-process-counter instrumentation pattern; there is no
span/trace API to call).
"""
from __future__ import annotations

import threading

_lock = threading.RLock()
_STATS = {
    "graphs_composed": 0, "traversals": 0, "searches": 0, "explanations": 0, "registry_lookups": 0,
    "adapter_failures": 0, "hidden_suppressed": 0, "orphan_relationships": 0, "cycles_avoided": 0,
    "traverse_ms_total": 0.0, "by_edge_type": {}, "by_source_failure": {}, "by_depth": {},
}


def note(kind: str, *, edge_type: str | None = None, source: str | None = None, depth: int | None = None,
         amount=1):
    with _lock:
        _STATS[kind] = _STATS.get(kind, 0) + amount
        if edge_type:
            _STATS["by_edge_type"][edge_type] = _STATS["by_edge_type"].get(edge_type, 0) + 1
        if source:
            _STATS["by_source_failure"][source] = _STATS["by_source_failure"].get(source, 0) + 1
        if depth is not None:
            key = str(depth)
            _STATS["by_depth"][key] = _STATS["by_depth"].get(key, 0) + 1


def note_ms(ms: float):
    with _lock:
        _STATS["traverse_ms_total"] += ms


def knowledge_stats() -> dict:
    with _lock:
        s = dict(_STATS)
        s["by_edge_type"] = dict(_STATS["by_edge_type"])
        s["by_source_failure"] = dict(_STATS["by_source_failure"])
        s["by_depth"] = dict(_STATS["by_depth"])
    traversals = s["traversals"] or 0
    s["avg_traverse_ms"] = round(s["traverse_ms_total"] / traversals, 1) if traversals else 0.0
    del s["traverse_ms_total"]
    return s


def reset_stats():
    with _lock:
        for k, v in list(_STATS.items()):
            _STATS[k] = {} if isinstance(v, dict) else (0.0 if isinstance(v, float) else 0)
