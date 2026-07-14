"""Release 0.9.9 Phase 3 — provider-abstraction consolidation tests.

Prove the single canonical registry (`ProviderRegistry`) backs every retained
provider path (portal identity, e-signature, notification, tax filing) and that
the duplicate per-domain registry class was removed without behavior change.
"""
import pytest


# --- Canonical registry ------------------------------------------------------

def test_provider_registry_register_and_get():
    from app.portal.providers import ProviderRegistry

    class _P:
        key = "demo"

    reg = ProviderRegistry("Demo provider")
    provider = _P()
    reg.register(provider)
    assert reg.get("demo") is provider


def test_provider_registry_unknown_key_uses_label():
    from app.portal.providers import ProviderRegistry

    reg = ProviderRegistry("Demo provider")
    with pytest.raises(ValueError) as excinfo:
        reg.get("missing")
    assert "Demo provider 'missing' is not configured" == str(excinfo.value)


def test_duplicate_signature_registry_class_removed():
    """The bespoke SignatureProviderRegistry is gone; signatures reuses the canonical one."""
    import app.portal.signatures as signatures
    from app.portal.providers import ProviderRegistry

    assert getattr(signatures, "SignatureProviderRegistry", None) is None
    assert isinstance(signatures.registry, ProviderRegistry)


def test_identity_registry_alias_preserved():
    """Backwards-compatible class alias still resolves to the canonical registry."""
    from app.portal.providers import (
        PORTAL_IDENTITY_PROVIDERS,
        PortalIdentityProviderRegistry,
        ProviderRegistry,
    )

    assert PortalIdentityProviderRegistry is ProviderRegistry
    assert isinstance(PORTAL_IDENTITY_PROVIDERS, ProviderRegistry)


# --- Portal identity provider path -------------------------------------------

def test_portal_identity_registry_error_message_unchanged():
    from app.portal.providers import PORTAL_IDENTITY_PROVIDERS

    with pytest.raises(ValueError) as excinfo:
        PORTAL_IDENTITY_PROVIDERS.get("nope")
    assert "Portal identity provider 'nope' is not configured" == str(excinfo.value)


# --- Signature provider path -------------------------------------------------

def test_signature_registry_error_message_unchanged():
    from app.portal.signatures import registry

    with pytest.raises(ValueError) as excinfo:
        registry.get("nope")
    assert "Signature provider 'nope' is not configured" == str(excinfo.value)


def test_signature_registry_register_and_get():
    from app.portal.providers import SignatureProvider, SignatureResult
    from app.portal.signatures import registry

    class _FakeSig(SignatureProvider):
        key = "phase3-fake"
        def create_request(self, *, recipients, documents, callback_url, metadata):
            return SignatureResult("ext-1", "sent", {})
        def get_status(self, external_id):
            return SignatureResult(external_id, "sent", {})
        def cancel(self, external_id):
            return SignatureResult(external_id, "cancelled", {})

    provider = _FakeSig()
    registry.register(provider)
    assert registry.get("phase3-fake") is provider


# --- Notification provider path ----------------------------------------------

def test_notification_providers_behavior_unchanged():
    from app.portal.providers import NOTIFICATION_PROVIDERS

    in_app = NOTIFICATION_PROVIDERS["in_app"].deliver(
        recipient="x", title="t", body="b", metadata={}
    )
    assert in_app == {"delivered": True, "channel": "in_app"}

    for channel in ("email", "sms", "push"):
        result = NOTIFICATION_PROVIDERS[channel].deliver(
            recipient="x", title="t", body="b", metadata={}
        )
        assert result["delivered"] is False
        assert result["reason"] == "provider_not_configured"
        assert result["channel"] == channel


# --- Tax filing provider path (reserved, unwired) ----------------------------

def test_tax_filing_manual_provider_path():
    from app.services.tax_filing_providers import FILING_PROVIDERS, ManualFilingProvider

    provider = FILING_PROVIDERS["manual"]
    assert isinstance(provider, ManualFilingProvider)

    submitted = provider.submit({"any": "payload"})
    assert submitted.status == "submitted"
    assert submitted.metadata == {"mode": "manual"}

    status = provider.status("ext-123")
    assert status.status == "ready"
    assert status.external_id == "ext-123"
