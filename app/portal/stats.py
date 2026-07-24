"""In-process operational counters for the Client Portal (Phase D.43).

Low-cardinality aggregates ONLY — never person/household ids, emails, document names, message subjects,
request-answer text, or tokens. These back the low-cardinality analytics metrics + internal diagnostics.
"""
from __future__ import annotations

import threading

_lock = threading.RLock()
_STATS = {
    "composition": 0, "auth_success": 0, "auth_failure": 0, "activations": 0, "recoveries": 0,
    "scope_denials": 0, "upload_success": 0, "upload_failure": 0, "downloads": 0, "messages_sent": 0,
    "appointment_requests": 0, "consents_accepted": 0, "consents_withdrawn": 0,
    "notification_failures": 0, "suppressed_sections": 0, "compose_ms_total": 0.0, "by_section": {},
}


def note(kind: str, *, section: str | None = None, amount=1):
    with _lock:
        _STATS[kind] = _STATS.get(kind, 0) + amount
        if section:
            _STATS["by_section"][section] = _STATS["by_section"].get(section, 0) + 1


def note_ms(ms: float):
    with _lock:
        _STATS["compose_ms_total"] += ms


def portal_stats() -> dict:
    with _lock:
        s = dict(_STATS)
        s["by_section"] = dict(_STATS["by_section"])
    comps = s["composition"] or 0
    s["avg_composition_ms"] = round(s["compose_ms_total"] / comps, 1) if comps else 0.0
    auth = s["auth_success"] + s["auth_failure"]
    s["login_failure_rate"] = round(s["auth_failure"] / auth * 100, 1) if auth else None
    ups = s["upload_success"] + s["upload_failure"]
    s["upload_failure_rate"] = round(s["upload_failure"] / ups * 100, 1) if ups else None
    del s["compose_ms_total"]
    return s


def reset_stats():
    with _lock:
        for k, v in list(_STATS.items()):
            _STATS[k] = {} if isinstance(v, dict) else 0
        _STATS["compose_ms_total"] = 0.0
