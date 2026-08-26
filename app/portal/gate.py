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
    # CONTROLLED SYNTHETIC TESTING ONLY. Lets the deterministic LOCAL identity provider register even
    # after production sign-off, so an authorized synthetic test can sign in without a real IdP. It does
    # NOT affect production_ready(), does not open any portal surface, and is NOT a substitute for the
    # real external identity provider that CLIENT_PORTAL_COMPLIANCE_GATE.md still requires before any
    # real client is onboarded. Must be returned to False before real-client onboarding.
    "portal.local_identity_provider_enabled": False,
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


def production_identity_provider_available() -> bool:
    """Whether a PRODUCTION-capable external identity provider is registered.

    The deterministic local/test provider is excluded by construction (``production_capable`` is False
    on it), so a synthetic provider can never satisfy this."""
    try:
        from app.portal.providers import PORTAL_IDENTITY_PROVIDERS
        return bool(PORTAL_IDENTITY_PROVIDERS.production_capable())
    except Exception:
        return False


def production_ready() -> bool:
    """External production access is permitted ONLY when the portal is enabled, compliance has signed
    off, AND a real external identity provider is registered.

    The third condition exists because the first two could previously report "production ready" with no
    way for any client to authenticate — sign-off implied usable external access that did not exist.
    The synthetic local provider never satisfies it. Blocked by default."""
    return (portal_enabled() and gate("portal.production_signed_off")
            and production_identity_provider_available())


def gate_status() -> dict:
    return {name: gate(name) for name in GATES}
