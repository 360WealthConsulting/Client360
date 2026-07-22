"""Declared schema for the Phase D.25 Enterprise Security platform.

Mirrors the live schema created by migration ``w7a8b9c0d1e2``. Enterprise Security is a new
authoritative SECURITY domain that owns **security metadata only** — security & hardening policies,
security configuration baselines, authentication/identity/federation providers, secret references,
certificate references, security exceptions, security incidents, and security findings — plus an
append-only audit ledger (``security_events``). It **owns no business records** and is **never a
source of truth** for business entities.

It **references** users/roles/capabilities/authentication/Microsoft 365 identity/Enterprise
Integration/Data Governance/Compliance/Automation/Workflow/Timeline/Audit, and **reuses** the
existing authentication, RBAC, record-scope, Fernet crypto, and audit hash-chain — it **never
replaces** login/OAuth, **never duplicates** an encryption helper, and **never stores a plaintext
secret** (a secret reference is a pointer to an existing encrypted store or Fernet ciphertext).
Certificate references are metadata only (fingerprint/serial), never a private key/PEM.
``security_events`` is the append-only audit ledger (trigger-blocked BEFORE UPDATE OR DELETE).
"""
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    func,
)

# Deterministic controlled vocabularies (metadata only).
POLICY_TYPES = ("security", "session", "password", "mfa", "access", "capability", "role", "api",
                "encryption", "key_rotation", "authentication", "federation")
POLICY_STATUSES = ("draft", "active", "approved", "retired")
CONFIG_CATEGORIES = ("authentication", "authorization", "session", "crypto", "network", "audit",
                     "hardening")
PROVIDER_KINDS = ("authentication", "identity", "federation")
PROVIDER_PROTOCOLS = ("oauth2", "oidc", "saml", "msal", "password", "api_key", "certificate")
PROVIDER_STATUSES = ("configured", "enabled", "disabled")
SECRET_REFERENCE_KINDS = ("microsoft_account", "integration_credential", "encrypted_secret",
                          "external_vault", "none")
SECRET_STATUSES = ("active", "rotating", "revoked", "expired")
ROTATION_SCHEDULES = ("manual", "monthly", "quarterly", "semiannual", "annual")
CERT_STATUSES = ("valid", "expiring", "expired", "revoked")
EXCEPTION_STATUSES = ("requested", "approved", "denied", "expired", "revoked")
INCIDENT_SEVERITIES = ("low", "medium", "high", "critical")
INCIDENT_STATUSES = ("open", "investigating", "contained", "resolved", "closed")
FINDING_STATUSES = ("open", "acknowledged", "remediated", "accepted", "false_positive")
FINDING_SOURCES = ("scan", "manual", "governance", "automation")
FINDING_SEVERITIES = ("info", "low", "medium", "high", "critical")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def define_security_tables(metadata: MetaData):
    # --- policies (unified: session/password/mfa/access/capability/role/api/encryption/...) -----
    policies = Table(
        "security_policies", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("policy_type", Text, nullable=False, server_default="security"),
        Column("status", Text, nullable=False, server_default="draft"),
        Column("version", Integer, nullable=False, server_default="1"),
        Column("config", JSON),                      # deterministic policy configuration (no secrets)
        Column("description", Text),
        Column("approved_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("approved_at", DateTime(timezone=True)),
        Column("effective_at", DateTime(timezone=True)),
        Column("policy_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("policy_type", POLICY_TYPES), name="ck_security_policy_type"),
        CheckConstraint(_in("status", POLICY_STATUSES), name="ck_security_policy_status"),
    )
    # --- configuration baselines (platform hardening) ------------------------------------------
    configurations = Table(
        "security_configurations", metadata,
        Column("id", Integer, primary_key=True),
        Column("config_key", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("category", Text, nullable=False, server_default="hardening"),
        Column("value", JSON),                       # configuration value (no secrets)
        Column("baseline", JSON),                    # recommended baseline value
        Column("applied", Boolean, nullable=False, server_default="false"),
        Column("description", Text),
        Column("configuration_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("category", CONFIG_CATEGORIES), name="ck_security_config_category"),
    )
    # --- identity / authentication / federation providers (metadata; disabled by default) ------
    providers = Table(
        "security_identity_providers", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("provider_kind", Text, nullable=False, server_default="authentication"),
        Column("protocol", Text, nullable=False, server_default="oauth2"),
        Column("status", Text, nullable=False, server_default="configured"),
        Column("enabled", Boolean, nullable=False, server_default="false"),  # disabled by default
        # Optional pointer to an existing auth store (never replaces login/OAuth).
        Column("microsoft_account_reference", Boolean, nullable=False, server_default="false"),
        Column("config", JSON),                      # non-secret configuration
        Column("credential_reference_id", Integer,
               ForeignKey("security_secret_references.id", ondelete="SET NULL")),
        Column("description", Text),
        Column("provider_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("provider_kind", PROVIDER_KINDS), name="ck_security_provider_kind"),
        CheckConstraint(_in("protocol", PROVIDER_PROTOCOLS), name="ck_security_provider_protocol"),
        CheckConstraint(_in("status", PROVIDER_STATUSES), name="ck_security_provider_status"),
    )
    # --- secret references (pointer or Fernet ciphertext — NEVER plaintext) --------------------
    secret_references = Table(
        "security_secret_references", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("reference_kind", Text, nullable=False, server_default="encrypted_secret"),
        Column("reference_id", Integer),             # e.g. microsoft_accounts.id / integration cred id
        Column("secret_ciphertext", Text),           # Fernet only — NEVER plaintext (optional)
        Column("owner_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("algorithm", Text),                   # e.g. fernet | aes-256-gcm (metadata)
        Column("storage_reference", Text),           # external vault path/uri (metadata)
        Column("rotation_schedule", Text, nullable=False, server_default="manual"),
        Column("last_rotated_at", DateTime(timezone=True)),
        Column("next_rotation_at", DateTime(timezone=True)),
        Column("expires_at", DateTime(timezone=True)),
        Column("status", Text, nullable=False, server_default="active"),
        Column("rotation_policy_id", Integer,
               ForeignKey("security_policies.id", ondelete="SET NULL")),
        Column("secret_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("reference_kind", SECRET_REFERENCE_KINDS), name="ck_security_secret_kind"),
        CheckConstraint(_in("rotation_schedule", ROTATION_SCHEDULES), name="ck_security_secret_rotation"),
        CheckConstraint(_in("status", SECRET_STATUSES), name="ck_security_secret_status"),
    )
    # --- certificate references (metadata only — never a private key/PEM) ----------------------
    certificates = Table(
        "security_certificate_references", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("subject", Text),
        Column("issuer", Text),
        Column("serial", Text),
        Column("fingerprint", Text),                 # sha256 fingerprint (public identifier)
        Column("not_before", DateTime(timezone=True)),
        Column("not_after", DateTime(timezone=True)),
        Column("status", Text, nullable=False, server_default="valid"),
        Column("storage_reference", Text),           # where the cert/key actually live (metadata)
        Column("last_renewed_at", DateTime(timezone=True)),
        Column("certificate_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", CERT_STATUSES), name="ck_security_cert_status"),
    )
    # --- security exceptions (approved deviations from a policy) --------------------------------
    exceptions = Table(
        "security_exceptions", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("title", Text, nullable=False),
        Column("policy_id", Integer, ForeignKey("security_policies.id", ondelete="SET NULL")),
        Column("justification", Text),
        Column("scope", Text),
        Column("status", Text, nullable=False, server_default="requested"),
        Column("requested_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("approved_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("approved_at", DateTime(timezone=True)),
        Column("expires_at", DateTime(timezone=True)),
        Column("exception_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", EXCEPTION_STATUSES), name="ck_security_exception_status"),
    )
    # --- security incidents (lifecycle; optional client anchor for the timeline) ---------------
    incidents = Table(
        "security_incidents", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("title", Text, nullable=False),
        Column("category", Text),
        Column("severity", Text, nullable=False, server_default="medium"),
        Column("status", Text, nullable=False, server_default="open"),
        Column("summary", Text),
        Column("owner_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("detected_at", DateTime(timezone=True)),
        Column("contained_at", DateTime(timezone=True)),
        Column("resolved_at", DateTime(timezone=True)),
        # Optional client anchor (a client-scoped incident) for guarded timeline publication.
        Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("household_id", Integer, ForeignKey("households.id", ondelete="SET NULL")),
        Column("incident_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("severity", INCIDENT_SEVERITIES), name="ck_security_incident_severity"),
        CheckConstraint(_in("status", INCIDENT_STATUSES), name="ck_security_incident_status"),
    )
    # --- security findings (references Governance findings; Governance stays authoritative) -----
    findings = Table(
        "security_findings", metadata,
        Column("id", Integer, primary_key=True),
        Column("title", Text, nullable=False),
        Column("finding_type", Text, nullable=False, server_default="manual"),
        Column("severity", Text, nullable=False, server_default="medium"),
        Column("status", Text, nullable=False, server_default="open"),
        Column("source", Text, nullable=False, server_default="manual"),
        Column("detail", Text),
        # References (never owns): a governance finding, a policy, an incident, a secret, a cert.
        Column("governance_finding_id", Integer),
        Column("policy_id", Integer, ForeignKey("security_policies.id", ondelete="SET NULL")),
        Column("incident_id", Integer, ForeignKey("security_incidents.id", ondelete="SET NULL")),
        Column("secret_reference_id", Integer,
               ForeignKey("security_secret_references.id", ondelete="SET NULL")),
        Column("certificate_reference_id", Integer,
               ForeignKey("security_certificate_references.id", ondelete="SET NULL")),
        Column("resolved_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("resolved_at", DateTime(timezone=True)),
        Column("finding_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("severity", FINDING_SEVERITIES), name="ck_security_finding_severity"),
        CheckConstraint(_in("status", FINDING_STATUSES), name="ck_security_finding_status"),
        CheckConstraint(_in("source", FINDING_SOURCES), name="ck_security_finding_source"),
    )
    # --- append-only audit ledger (polymorphic; no FK so parent deletes never touch it) --------
    events = Table(
        "security_events", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("entity_type", Text, nullable=False),   # policy | provider | secret | incident ...
        Column("entity_id", Integer, nullable=False),
        Column("event_type", Text, nullable=False),
        Column("from_status", Text),
        Column("to_status", Text),
        Column("actor_user_id", Integer),
        Column("payload", JSON),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    return {
        "security_policies": policies,
        "security_configurations": configurations,
        "security_identity_providers": providers,
        "security_secret_references": secret_references,
        "security_certificate_references": certificates,
        "security_exceptions": exceptions,
        "security_incidents": incidents,
        "security_findings": findings,
        "security_events": events,
    }
