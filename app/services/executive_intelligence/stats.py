"""In-process Executive Reporting counters (Phase D.48).

Low-cardinality aggregates ONLY — never client identifiers, metric values, or business data. These back the
low-cardinality analytics metrics (registered in the SINGLE Analytics Registry — no second metrics store) +
internal diagnostics + observability instrumentation.
"""
from __future__ import annotations

import threading

_lock = threading.RLock()
_STATS = {
    "dashboards_composed": 0, "widgets_composed": 0, "registry_lookups": 0, "aggregation_failures": 0,
    "authorization_failures": 0, "restricted_widgets": 0, "missing_explainability": 0,
    "compose_ms_total": 0.0, "by_dashboard": {}, "by_widget_failure": {},
}


def note(kind: str, *, dashboard: str | None = None, widget: str | None = None, amount=1):
    with _lock:
        _STATS[kind] = _STATS.get(kind, 0) + amount
        if dashboard:
            _STATS["by_dashboard"][dashboard] = _STATS["by_dashboard"].get(dashboard, 0) + 1
        if widget:
            _STATS["by_widget_failure"][widget] = _STATS["by_widget_failure"].get(widget, 0) + 1


def note_ms(ms: float):
    with _lock:
        _STATS["compose_ms_total"] += ms


def reporting_stats() -> dict:
    with _lock:
        s = dict(_STATS)
        s["by_dashboard"] = dict(_STATS["by_dashboard"])
        s["by_widget_failure"] = dict(_STATS["by_widget_failure"])
    comps = s["dashboards_composed"] or 0
    s["avg_compose_ms"] = round(s["compose_ms_total"] / comps, 1) if comps else 0.0
    del s["compose_ms_total"]
    return s


def reset_stats():
    with _lock:
        for k, v in list(_STATS.items()):
            _STATS[k] = {} if isinstance(v, dict) else (0.0 if isinstance(v, float) else 0)
