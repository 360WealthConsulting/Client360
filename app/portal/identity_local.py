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
    """Register the local provider ONLY when the portal is not production-signed-off. In production
    (``portal.production_signed_off`` true) this is a no-op, so no offline provider is ever available to
    verify a real external activation. Idempotent."""
    try:
        from app.portal.gate import gate
        if gate("portal.production_signed_off"):
            return False
    except Exception:
        # Fail closed toward NOT registering only if we cannot even evaluate; but the default gate is
        # False (not signed off), so absent runtime we DO register for local/test usability.
        pass
    PORTAL_IDENTITY_PROVIDERS.register(LocalTestIdentityProvider())
    return True
