from app.integrations.identity.oidc import OidcIdentityProvider
from app.security.models import Principal
from app.security.policy import has_record_scope
from app.security.redaction import redact_metadata
from app.security.identity_utils import normalize_email

def test_capability_is_composed_not_role_named():
    principal = Principal(1, "advisor@example.com", "Advisor", frozenset({"client.read", "task.write"}))
    assert principal.can("client.read")
    assert not principal.can("identity.manage")

def test_normalized_identity_email():
    assert normalize_email(" Advisor@Example.COM ") == "advisor@example.com"

def test_audit_metadata_redacts_sensitive_values():
    result = redact_metadata({"status": "active", "access_token": "secret", "message_body": "private", "ssn_last4": "1234"})
    assert result == {"status": "active", "access_token": "[REDACTED]", "message_body": "[REDACTED]", "ssn_last4": "[REDACTED]"}

def test_oidc_authorization_adapter_is_provider_neutral(monkeypatch):
    provider = OidcIdentityProvider("https://identity.example", "client", "secret")
    monkeypatch.setattr(provider, "_discovery", lambda: {"authorization_endpoint": "https://identity.example/authorize"})
    url = provider.authorization_url(state="state", redirect_uri="https://client360.example/auth/callback")
    assert url.startswith("https://identity.example/authorize?")
    assert "response_type=code" in url and "state=state" in url

def test_oidc_requires_configuration(monkeypatch):
    for key in ("OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET"): monkeypatch.delenv(key, raising=False)
    try: OidcIdentityProvider()
    except RuntimeError as exc: assert "OIDC_ISSUER" in str(exc)
    else: raise AssertionError("Missing OIDC configuration must fail closed")

def test_record_scope_honors_explicit_bypass_capability():
    principal = Principal(1, "admin@example.com", "Admin", frozenset({"record.read_all"}))
    assert has_record_scope(None, principal, "person", 99, record_assignments=None)

def test_record_scope_uses_active_assignment():
    from sqlalchemy import column, table
    class Connection:
        def scalar(self, statement): return 42
    assignments = table("record_assignments", column("id"), column("user_id"), column("entity_type"), column("entity_id"), column("effective_date"), column("inactive_date"))
    principal = Principal(1, "advisor@example.com", "Advisor", frozenset())
    assert has_record_scope(Connection(), principal, "person", 99, record_assignments=assignments)

def test_write_scope_requires_write_bypass():
    from sqlalchemy import column, table
    principal = Principal(1, "reader@example.com", "Reader", frozenset({"record.read_all"}))
    class Connection:
        def scalar(self, statement): return None
    assignments = table("record_assignments", column("id"), column("user_id"), column("entity_type"), column("entity_id"), column("effective_date"), column("inactive_date"))
    assert not has_record_scope(Connection(), principal, "person", 99, record_assignments=assignments, write=True)

def test_seeded_roles_reference_only_catalog_capabilities():
    from migrations.versions.c410f4a1b2c3_add_firm_identity_rbac_audit import CAPABILITIES, ROLE_CAPABILITIES
    assert all(set(items) <= set(CAPABILITIES) for items in ROLE_CAPABILITIES.values())
    assert set(ROLE_CAPABILITIES["administrator"]) == set(CAPABILITIES)
