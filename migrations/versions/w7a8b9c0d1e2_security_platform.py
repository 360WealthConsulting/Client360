"""Enterprise Security platform (Phase D.25).

Enterprise Security is a new authoritative SECURITY domain that owns security metadata only —
security & hardening policies, security configuration baselines, authentication/identity/federation
providers, secret references, certificate references, security exceptions, incidents, and findings —
plus an append-only audit ledger. It **owns no business records**, **references** users/roles/
capabilities/authentication/Microsoft 365 identity/Enterprise Integration/Data Governance/Compliance,
and **reuses** the existing authentication, RBAC, record-scope, Fernet crypto, and audit hash-chain.
It **never replaces** login/OAuth, **never duplicates** an encryption helper, and **never stores a
plaintext secret** (a secret reference is a pointer to an existing encrypted store or Fernet
ciphertext). Certificate references are metadata only (no private key/PEM).

Tables (9). Also **widens the Automation JOB_TYPES CHECK constraints** to add a ``security_review``
job type (so Automation may run scheduled rotation/certificate/policy reviews). Seeds 5 security.*
capabilities and a small set of hardening configuration baselines. Additive and reversible. Single
Alembic head (down ``v2a3b4c5d6e7``).
"""
import sqlalchemy as sa
from alembic import op

revision = "w7a8b9c0d1e2"
down_revision = "v2a3b4c5d6e7"
branch_labels = None
depends_on = None

_POLICY_TYPES = ("security", "session", "password", "mfa", "access", "capability", "role", "api",
                 "encryption", "key_rotation", "authentication", "federation")
_POLICY_STATUSES = ("draft", "active", "approved", "retired")
_CONFIG_CATEGORIES = ("authentication", "authorization", "session", "crypto", "network", "audit",
                      "hardening")
_PROVIDER_KINDS = ("authentication", "identity", "federation")
_PROVIDER_PROTOCOLS = ("oauth2", "oidc", "saml", "msal", "password", "api_key", "certificate")
_PROVIDER_STATUSES = ("configured", "enabled", "disabled")
_SECRET_REFERENCE_KINDS = ("microsoft_account", "integration_credential", "encrypted_secret",
                           "external_vault", "none")
_SECRET_STATUSES = ("active", "rotating", "revoked", "expired")
_ROTATION_SCHEDULES = ("manual", "monthly", "quarterly", "semiannual", "annual")
_CERT_STATUSES = ("valid", "expiring", "expired", "revoked")
_EXCEPTION_STATUSES = ("requested", "approved", "denied", "expired", "revoked")
_INCIDENT_SEVERITIES = ("low", "medium", "high", "critical")
_INCIDENT_STATUSES = ("open", "investigating", "contained", "resolved", "closed")
_FINDING_STATUSES = ("open", "acknowledged", "remediated", "accepted", "false_positive")
_FINDING_SOURCES = ("scan", "manual", "governance", "automation")
_FINDING_SEVERITIES = ("info", "low", "medium", "high", "critical")

# Automation JOB_TYPES (current 17 = 13 base + 3 governance + integration_sync) widened.
_JOB_TYPES_OLD = ("run_report_schedule", "report_schedules_sweep", "capture_analytics_snapshots",
                  "launch_workflow", "workflow_sla_sweep", "dispatch_notifications", "dispatch_outbox",
                  "send_communication", "m365_mail_sync", "m365_calendar_sync", "m365_document_sync",
                  "governance_quality_scan", "governance_stale_scan", "governance_retention_review",
                  "integration_sync", "maintenance", "custom")
_JOB_TYPES_NEW = _JOB_TYPES_OLD + ("security_review",)


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def _set_job_type_check(table, constraint, values):
    op.drop_constraint(constraint, table, type_="check")
    op.create_check_constraint(constraint, table, _in("job_type", values))


_CAPS = (
    ("security.view", "View security policies, providers, secret/certificate references, incidents, "
     "and findings.", False, ("administrator", "operations", "compliance")),
    ("security.manage", "Create and configure security policies, providers, secret/certificate "
     "references, exceptions, incidents, and findings.", False, ("administrator", "operations")),
    ("security.execute", "Approve policies, rotate secrets, renew certificates, resolve incidents, "
     "and run security reviews.", False, ("administrator", "operations")),
    ("security.audit", "View security audit history and secret/certificate reference metadata.", True,
     ("administrator", "compliance")),
    ("security.admin", "Administer the security platform.", True, ("administrator",)),
)

# (config_key, name, category, baseline JSON-as-text)
_CONFIG_SEED = (
    ("session.timeout_hours", "Session timeout (hours)", "session", "8"),
    ("session.token_hash_only", "Sessions store only the token hash", "session", "true"),
    ("mfa.required", "MFA required for staff authentication", "authentication", "true"),
    ("crypto.fernet_fail_closed", "Field crypto fails closed when key absent", "crypto", "true"),
    ("audit.hash_chain_enabled", "Audit events are hash-chained and append-only", "audit", "true"),
    ("network.security_headers", "Standard security response headers enforced", "network", "true"),
    ("network.csrf_same_origin", "Same-origin/CSRF check on state-changing methods", "network", "true"),
)


def upgrade():
    bind = op.get_bind()

    op.create_table(
        "security_policies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("policy_type", sa.Text, nullable=False, server_default="security"),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("config", sa.JSON),
        sa.Column("description", sa.Text),
        sa.Column("approved_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("effective_at", sa.DateTime(timezone=True)),
        sa.Column("policy_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("policy_type", _POLICY_TYPES), name="ck_security_policy_type"),
        sa.CheckConstraint(_in("status", _POLICY_STATUSES), name="ck_security_policy_status"),
    )
    op.create_index("ix_security_policies_type", "security_policies", ["policy_type"])

    op.create_table(
        "security_configurations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("config_key", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False, server_default="hardening"),
        sa.Column("value", sa.JSON),
        sa.Column("baseline", sa.JSON),
        sa.Column("applied", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("description", sa.Text),
        sa.Column("configuration_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("category", _CONFIG_CATEGORIES), name="ck_security_config_category"),
    )

    # secret references before providers/findings (both reference it).
    op.create_table(
        "security_secret_references",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("reference_kind", sa.Text, nullable=False, server_default="encrypted_secret"),
        sa.Column("reference_id", sa.Integer),
        sa.Column("secret_ciphertext", sa.Text),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("algorithm", sa.Text),
        sa.Column("storage_reference", sa.Text),
        sa.Column("rotation_schedule", sa.Text, nullable=False, server_default="manual"),
        sa.Column("last_rotated_at", sa.DateTime(timezone=True)),
        sa.Column("next_rotation_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("rotation_policy_id", sa.Integer, sa.ForeignKey("security_policies.id", ondelete="SET NULL")),
        sa.Column("secret_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("reference_kind", _SECRET_REFERENCE_KINDS), name="ck_security_secret_kind"),
        sa.CheckConstraint(_in("rotation_schedule", _ROTATION_SCHEDULES), name="ck_security_secret_rotation"),
        sa.CheckConstraint(_in("status", _SECRET_STATUSES), name="ck_security_secret_status"),
    )
    op.create_index("ix_security_secret_status", "security_secret_references", ["status"])

    op.create_table(
        "security_identity_providers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("provider_kind", sa.Text, nullable=False, server_default="authentication"),
        sa.Column("protocol", sa.Text, nullable=False, server_default="oauth2"),
        sa.Column("status", sa.Text, nullable=False, server_default="configured"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("microsoft_account_reference", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("config", sa.JSON),
        sa.Column("credential_reference_id", sa.Integer,
                  sa.ForeignKey("security_secret_references.id", ondelete="SET NULL")),
        sa.Column("description", sa.Text),
        sa.Column("provider_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("provider_kind", _PROVIDER_KINDS), name="ck_security_provider_kind"),
        sa.CheckConstraint(_in("protocol", _PROVIDER_PROTOCOLS), name="ck_security_provider_protocol"),
        sa.CheckConstraint(_in("status", _PROVIDER_STATUSES), name="ck_security_provider_status"),
    )

    op.create_table(
        "security_certificate_references",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("subject", sa.Text),
        sa.Column("issuer", sa.Text),
        sa.Column("serial", sa.Text),
        sa.Column("fingerprint", sa.Text),
        sa.Column("not_before", sa.DateTime(timezone=True)),
        sa.Column("not_after", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text, nullable=False, server_default="valid"),
        sa.Column("storage_reference", sa.Text),
        sa.Column("last_renewed_at", sa.DateTime(timezone=True)),
        sa.Column("certificate_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _CERT_STATUSES), name="ck_security_cert_status"),
    )

    op.create_table(
        "security_exceptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("policy_id", sa.Integer, sa.ForeignKey("security_policies.id", ondelete="SET NULL")),
        sa.Column("justification", sa.Text),
        sa.Column("scope", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="requested"),
        sa.Column("requested_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("approved_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("exception_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _EXCEPTION_STATUSES), name="ck_security_exception_status"),
    )

    op.create_table(
        "security_incidents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("category", sa.Text),
        sa.Column("severity", sa.Text, nullable=False, server_default="medium"),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("summary", sa.Text),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("detected_at", sa.DateTime(timezone=True)),
        sa.Column("contained_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("incident_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("severity", _INCIDENT_SEVERITIES), name="ck_security_incident_severity"),
        sa.CheckConstraint(_in("status", _INCIDENT_STATUSES), name="ck_security_incident_status"),
    )
    op.create_index("ix_security_incidents_status", "security_incidents", ["status"])

    op.create_table(
        "security_findings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("finding_type", sa.Text, nullable=False, server_default="manual"),
        sa.Column("severity", sa.Text, nullable=False, server_default="medium"),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("source", sa.Text, nullable=False, server_default="manual"),
        sa.Column("detail", sa.Text),
        sa.Column("governance_finding_id", sa.Integer),
        sa.Column("policy_id", sa.Integer, sa.ForeignKey("security_policies.id", ondelete="SET NULL")),
        sa.Column("incident_id", sa.Integer, sa.ForeignKey("security_incidents.id", ondelete="SET NULL")),
        sa.Column("secret_reference_id", sa.Integer,
                  sa.ForeignKey("security_secret_references.id", ondelete="SET NULL")),
        sa.Column("certificate_reference_id", sa.Integer,
                  sa.ForeignKey("security_certificate_references.id", ondelete="SET NULL")),
        sa.Column("resolved_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("finding_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("severity", _FINDING_SEVERITIES), name="ck_security_finding_severity"),
        sa.CheckConstraint(_in("status", _FINDING_STATUSES), name="ck_security_finding_status"),
        sa.CheckConstraint(_in("source", _FINDING_SOURCES), name="ck_security_finding_source"),
    )
    op.create_index("ix_security_findings_status", "security_findings", ["status"])

    # Append-only audit ledger (polymorphic; no FK so parent deletes never touch immutable rows).
    op.create_table(
        "security_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("from_status", sa.Text),
        sa.Column("to_status", sa.Text),
        sa.Column("actor_user_id", sa.Integer),
        sa.Column("payload", sa.JSON),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_security_events_entity", "security_events", ["entity_type", "entity_id"])
    op.execute(
        "CREATE OR REPLACE FUNCTION prevent_security_event_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'security_events are append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER security_events_immutable BEFORE UPDATE OR DELETE ON security_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_security_event_mutation()"
    )

    # Widen the Automation JOB_TYPES CHECKs so Automation may run security reviews (D.22 reuse).
    _set_job_type_check("automation_jobs", "ck_automation_job_type", _JOB_TYPES_NEW)
    _set_job_type_check("automation_job_templates", "ck_automation_template_job_type", _JOB_TYPES_NEW)

    # Seed hardening configuration baselines (idempotent; describe the enforced posture as metadata).
    for key, name, category, baseline in _CONFIG_SEED:
        if bind.execute(sa.text("SELECT id FROM security_configurations WHERE config_key=:k"),
                        {"k": key}).scalar() is None:
            bind.execute(sa.text(
                "INSERT INTO security_configurations (config_key, name, category, baseline, applied) "
                "VALUES (:k, :n, :c, :b, true)"),
                {"k": key, "n": name, "c": category, "b": baseline})

    # Seed capabilities (idempotent).
    for code, description, sensitive, roles in _CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is None:
            cid = bind.execute(
                sa.text("INSERT INTO capabilities (code, description, sensitive) "
                        "VALUES (:c, :d, :s) RETURNING id"),
                {"c": code, "d": description, "s": sensitive}).scalar()
        for role_code in roles:
            role_id = bind.execute(sa.text("SELECT id FROM roles WHERE code = :r"), {"r": role_code}).scalar()
            if role_id is None:
                continue
            exists = bind.execute(
                sa.text("SELECT 1 FROM role_capabilities WHERE role_id = :r AND capability_id = :c"),
                {"r": role_id, "c": cid}).scalar()
            if not exists:
                bind.execute(sa.text("INSERT INTO role_capabilities (role_id, capability_id) "
                                     "VALUES (:r, :c)"), {"r": role_id, "c": cid})


def downgrade():
    bind = op.get_bind()
    for code, _d, _s, _roles in _CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is not None:
            bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id = :c"), {"c": cid})
            bind.execute(sa.text("DELETE FROM capabilities WHERE id = :c"), {"c": cid})

    _set_job_type_check("automation_jobs", "ck_automation_job_type", _JOB_TYPES_OLD)
    _set_job_type_check("automation_job_templates", "ck_automation_template_job_type", _JOB_TYPES_OLD)

    op.execute("DROP TRIGGER IF EXISTS security_events_immutable ON security_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_security_event_mutation()")
    op.drop_table("security_events")
    op.drop_table("security_findings")
    op.drop_table("security_incidents")
    op.drop_table("security_exceptions")
    op.drop_table("security_certificate_references")
    op.drop_table("security_identity_providers")
    op.drop_table("security_secret_references")
    op.drop_table("security_configurations")
    op.drop_table("security_policies")
