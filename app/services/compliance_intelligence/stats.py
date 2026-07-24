"""In-process Compliance Intelligence counters (Phase D.47).

Low-cardinality aggregates ONLY — never person/household ids, names, reviewer names, or supervisory
evidence. These back the low-cardinality analytics metrics + internal diagnostics + observability
instrumentation (the platform's established in-process-counter pattern; no span/trace API).
"""
from __future__ import annotations

import threading

_lock = threading.RLock()
_STATS = {
    "reviews_composed": 0, "exceptions_composed": 0, "dashboards": 0, "summaries": 0,
    "suppressed": 0, "missing_evidence": 0, "authorization_failures": 0, "adapter_failures": 0,
    "compositions": 0, "registry_lookups": 0, "overdue_reviews": 0,
    "compose_ms_total": 0.0, "by_review_type": {}, "by_severity": {}, "by_source_failure": {},
}


def note(kind: str, *, review_type: str | None = None, severity: str | None = None,
         source: str | None = None, amount=1):
    with _lock:
        _STATS[kind] = _STATS.get(kind, 0) + amount
        if review_type:
            _STATS["by_review_type"][review_type] = _STATS["by_review_type"].get(review_type, 0) + 1
        if severity:
            _STATS["by_severity"][severity] = _STATS["by_severity"].get(severity, 0) + 1
        if source:
            _STATS["by_source_failure"][source] = _STATS["by_source_failure"].get(source, 0) + 1


def note_ms(ms: float):
    with _lock:
        _STATS["compose_ms_total"] += ms


def compliance_stats() -> dict:
    with _lock:
        s = dict(_STATS)
        s["by_review_type"] = dict(_STATS["by_review_type"])
        s["by_severity"] = dict(_STATS["by_severity"])
        s["by_source_failure"] = dict(_STATS["by_source_failure"])
    comps = s["compositions"] or 0
    s["avg_compose_ms"] = round(s["compose_ms_total"] / comps, 1) if comps else 0.0
    del s["compose_ms_total"]
    return s


def reset_stats():
    with _lock:
        for k, v in list(_STATS.items()):
            _STATS[k] = {} if isinstance(v, dict) else (0.0 if isinstance(v, float) else 0)
