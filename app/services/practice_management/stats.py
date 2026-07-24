"""In-process Practice Management counters (Phase D.49).

Low-cardinality aggregates ONLY — never client identifiers, resource names, workload values, or business
data. These back the low-cardinality analytics metrics (registered in the SINGLE Analytics Registry — no
second metrics store) + internal diagnostics + observability instrumentation.
"""
from __future__ import annotations

import threading

_lock = threading.RLock()
_STATS = {
    "dashboards_composed": 0, "panels_composed": 0, "summaries_composed": 0, "aggregation_failures": 0,
    "authorization_failures": 0, "restricted_panels": 0, "missing_explainability": 0,
    "compose_ms_total": 0.0, "by_dashboard": {}, "by_panel_failure": {},
}


def note(kind: str, *, dashboard: str | None = None, panel: str | None = None, amount=1):
    with _lock:
        _STATS[kind] = _STATS.get(kind, 0) + amount
        if dashboard:
            _STATS["by_dashboard"][dashboard] = _STATS["by_dashboard"].get(dashboard, 0) + 1
        if panel:
            _STATS["by_panel_failure"][panel] = _STATS["by_panel_failure"].get(panel, 0) + 1


def note_ms(ms: float):
    with _lock:
        _STATS["compose_ms_total"] += ms


def practice_stats() -> dict:
    with _lock:
        s = dict(_STATS)
        s["by_dashboard"] = dict(_STATS["by_dashboard"])
        s["by_panel_failure"] = dict(_STATS["by_panel_failure"])
    comps = s["dashboards_composed"] or 0
    s["avg_compose_ms"] = round(s["compose_ms_total"] / comps, 1) if comps else 0.0
    del s["compose_ms_total"]
    return s


def reset_stats():
    with _lock:
        for k, v in list(_STATS.items()):
            _STATS[k] = {} if isinstance(v, dict) else (0.0 if isinstance(v, float) else 0)
