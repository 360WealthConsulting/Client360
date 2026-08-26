"""Deterministic local/test portal identity provider (Phase D.43).

The portal delegates activation to an external identity provider (``PortalIdentityProvider.verify_activation``).
Production integrates a real IdP; there is no local password store. For local development, tests, and CI —
where no external identity provider exists — this deterministic provider lets activation and sign-in work
offline WITHOUT weakening production: it registers ONLY when the portal is not production-signed-off, so it
can never satisfy a real external activation in production.

The assertion format is a signed-in-spirit local token ``local:<subject>[:mfa]`` — deterministic, no
network, no secrets. It NEVER auto-links by email; linking a subject to a portal account remains the
explicit, audited ``accept_invitation`` step.
"""
from __future__ import annotations

from app.portal.providers import (
    PORTAL_IDENTITY_PROVIDERS,
    PortalIdentityProvider,
    PortalIdentityResult,
)

LOCAL_PROVIDER_KEY = "local"


class LocalTestIdentityProvider(PortalIdentityProvider):
    """Offline identity verification for local/test only. Accepts ``local:<subject>[:mfa]`` and echoes the
    subject; MFA is considered verified only when the assertion explicitly carries the ``mfa`` marker, so
    the MFA-required path stays exercisable and testable without a real IdP."""
    key = LOCAL_PROVIDER_KEY

    def verify_activation(self, assertion: str) -> PortalIdentityResult:
        if not assertion or not assertion.startswith("local:"):
            raise ValueError("Invalid activation assertion")
        parts = assertion.split(":")
        subject = parts[1].strip() if len(parts) > 1 else ""
        if not subject:
            raise ValueError("Invalid activation assertion")
        mfa_verified = len(parts) > 2 and parts[2] == "mfa"
        return PortalIdentityResult(subject=f"local:{subject}", mfa_verified=mfa_verified, email=None)


def register_local_provider_if_permitted():
    """Register the deterministic local provider when the deployment is entitled to one.

    Two independent conditions, evaluated through the governed runtime engine (never env vars):

    * ``portal.production_signed_off`` is FALSE — the long-standing local/dev/CI behaviour: the portal is
      not claiming production status, so an offline provider is appropriate and activation works offline.
    * ``portal.local_identity_provider_enabled`` is TRUE — an EXPLICIT, separately governed authorization
      to keep the local provider during a controlled synthetic test that runs *after* sign-off. Default
      False. This exists only because sign-off otherwise removes the sole provider, making even a
      synthetic test impossible; it is NOT a substitute for the real external IdP that
      docs/CLIENT_PORTAL_COMPLIANCE_GATE.md requires before any real client is onboarded, and it must be
      returned to False before real-client onboarding.

    Sign-off alone therefore still removes the provider (the production protection is intact), and
    ``portal.enabled`` never registers anything. Fails closed on an unresolvable runtime only for the
    explicit test gate; the sign-off half keeps its documented default (not signed off → register), so
    local and CI behaviour is unchanged. Idempotent — the registry is keyed by provider ``.key``."""
    try:
        from app.portal.gate import gate
        signed_off = gate("portal.production_signed_off")
        test_provider_authorized = gate("portal.local_identity_provider_enabled")
    except Exception:
        # Absent a resolvable runtime, fall back to the documented defaults: not signed off (so the
        # local provider registers for local/test usability) and the test gate OFF.
        signed_off, test_provider_authorized = False, False
    if signed_off and not test_provider_authorized:
        return False
    PORTAL_IDENTITY_PROVIDERS.register(LocalTestIdentityProvider())
    return True
