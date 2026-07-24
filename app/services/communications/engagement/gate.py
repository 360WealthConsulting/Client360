"""Engagement runtime gates (Phase D.44).

Every engagement surface is gated through the governed Runtime Engine
(``runtime.consumption.feature_enabled``) — no raw environment fallback. The composition reuses already
authorized, already visible data, so the advisor-facing gates default ON (feature-flaggable off), while the
external portal timeline defaults OFF (opt-in, conservative — mirrors the D.43 portal posture).
"""
from __future__ import annotations

GATES = {
    "communications.enabled": True,          # master switch for the engagement composition layer
    "advisor.timeline.enabled": True,        # advisor/staff engagement timeline surfaces
    "household.timeline.enabled": True,      # household engagement aggregation
    "engagement.search.enabled": True,       # unified communication search
    "portal.timeline.enabled": False,        # external client engagement timeline — opt-in
}


def gate(name: str) -> bool:
    """Runtime-evaluated gate. Never raises; falls back to the declared default."""
    default = GATES.get(name, False)
    try:
        from app.services.runtime import consumption
        return bool(consumption.feature_enabled(name, default=default, shim=True))
    except Exception:
        return default


def enabled() -> bool:
    return gate("communications.enabled")


def gate_status() -> dict:
    return {name: gate(name) for name in GATES}
