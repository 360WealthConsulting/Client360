"""Shared helpers + in-process telemetry for Advisor AI Assist (Phase D.42).

The in-process counters are low-cardinality operational aggregates ONLY — never client-specific data,
never prompt contents, never generated prose. They back the analytics metrics + diagnostics.
"""
from __future__ import annotations

import json
import threading

HUMAN_REVIEW_LABEL = "Advisor Assist — Review Required"
GENERATION_UNAVAILABLE = "AI generation unavailable — showing source facts only."

_lock = threading.RLock()
_STATS = {
    "requests": 0, "success": 0, "failures": 0, "refusals": 0, "timeouts": 0,
    "provider_failures": 0, "malformed": 0, "truncations": 0, "citations": 0,
    "unsupported_questions": 0, "latency_ms_total": 0.0,
    "by_capability": {}, "by_refusal": {},
}


def as_json(payload):
    return json.loads(json.dumps(payload if payload is not None else {}, default=str))


def note(kind: str, *, capability: str | None = None, amount=1):
    with _lock:
        _STATS[kind] = _STATS.get(kind, 0) + amount
        if capability:
            _STATS["by_capability"][capability] = _STATS["by_capability"].get(capability, 0) + 1


def note_refusal(category: str):
    with _lock:
        _STATS["refusals"] += 1
        _STATS["by_refusal"][category] = _STATS["by_refusal"].get(category, 0) + 1


def note_latency(ms: float):
    with _lock:
        _STATS["latency_ms_total"] += ms


def assist_stats() -> dict:
    with _lock:
        s = dict(_STATS)
        s["by_capability"] = dict(_STATS["by_capability"])
        s["by_refusal"] = dict(_STATS["by_refusal"])
    total = s["requests"] or 0
    s["avg_latency_ms"] = round(s["latency_ms_total"] / total, 1) if total else 0.0
    s["success_rate"] = round(s["success"] / total * 100, 1) if total else None
    s["refusal_rate"] = round(s["refusals"] / total * 100, 1) if total else None
    s["citation_coverage"] = round(s["citations"] / s["success"] * 100, 1) if s["success"] else None
    del s["latency_ms_total"]
    return s


def reset_stats():
    with _lock:
        for k, v in list(_STATS.items()):
            _STATS[k] = {} if isinstance(v, dict) else 0
        _STATS["latency_ms_total"] = 0.0
