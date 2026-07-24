"""Client Portal runtime + production gates (Phase D.43).

Every externally-facing portal capability is gated through the governed Runtime Engine
(``runtime.consumption.feature_enabled``) with a production-safe ``default=False`` — the portal is OFF for
production until a runtime snapshot explicitly enables it AND a compliance sign-off is recorded. There is
NO raw environment-variable fallback: the runtime snapshot is the sole evaluator; the ``default`` is the
legacy production-safe behavior. ``production_ready`` AND-gates external access on the compliance sign-off
so external client data is never served without review.
"""
from __future__ import annotations

# Portal feature gates and their production-safe defaults (OFF).
GATES = {
    "portal.enabled": False,
    "portal.household_enabled": False,
    "portal.documents.download_enabled": False,
    "portal.documents.upload_enabled": False,
    "portal.messaging_enabled": False,
    "portal.appointments_enabled": False,
    "portal.financial_summary_enabled": False,
    "portal.forms_enabled": False,
    "portal.mfa_required": True,
    "portal.production_signed_off": False,   # the compliance sign-off gate — blocked by default
}


def gate(name: str) -> bool:
    """Runtime-evaluated portal gate. Never raises; falls back to the production-safe default (OFF)."""
    default = GATES.get(name, False)
    try:
        from app.services.runtime import consumption
        return bool(consumption.feature_enabled(name, default=default, shim=True))
    except Exception:
        return default


def config(key: str, default=None):
    try:
        from app.services.runtime import consumption
        return consumption.config_value(key, default=default, shim=True)
    except Exception:
        return default


def portal_enabled() -> bool:
    return gate("portal.enabled")


def production_ready() -> bool:
    """External production access is permitted ONLY when the portal is enabled AND compliance has signed
    off. Blocked by default — local/test implementation proceeds behind the disabled gate."""
    return portal_enabled() and gate("portal.production_signed_off")


def gate_status() -> dict:
    return {name: gate(name) for name in GATES}
