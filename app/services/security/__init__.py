"""Enterprise Security domain (Phase D.25) — authoritative security-metadata domain.

Owns security metadata only: security & hardening policies, security configuration baselines,
authentication/identity/federation providers, secret references, certificate references, security
exceptions, incidents, and findings — plus an append-only ``security_events`` audit ledger. It owns
no business records and is never a source of truth for business entities.

Reuses (never replaces/duplicates) the existing authentication (``app.security.service``), RBAC and
record scope (``app.security.authorization``), Microsoft 365 identity, the Fernet field crypto (a new
fail-closed ``app.security.security_crypto`` helper), and the audit hash-chain
(``app.security.audit.write_audit_event``). Never stores a plaintext secret; certificate references
are metadata only.
"""
