"""Enterprise Integration platform (Phase D.24).

Integration is a new authoritative INTEGRATION domain that owns integration metadata only —
providers, connectors, credential references, synchronization profiles/runs/conflicts, webhook
endpoints/subscriptions/deliveries, API clients/usage, event definitions/subscriptions, and
import/export profiles — plus an append-only audit ledger. It **owns no business records**, reuses
the existing importers / Microsoft 365 OAuth / Fernet encryption / transactional outbox / automation
dispatch, **never duplicates provider logic**, **never stores a plaintext secret**, and adds **no
external broker**. Webhook delivery is metadata only (no outbound HTTP this phase).

Tables (15). Also **widens the Automation JOB_TYPES CHECK constraints** to add an ``integration_sync``
job type (so Automation may execute scheduled synchronization). Seeds 5 integration.* capabilities,
12 disabled-by-default providers (Microsoft 365, Schwab, AssetMark, Wealthbox, TaxDome, Drake, ADP,
Guideline, Betterment 401(k), QuickBooks, IRS, State e-file), and 2 event definitions. Additive and
reversible. Single Alembic head (down ``u1f2a3b4c5d6``).
"""
import sqlalchemy as sa
from alembic import op

revision = "v2a3b4c5d6e7"
down_revision = "u1f2a3b4c5d6"
branch_labels = None
depends_on = None

_PROVIDER_TYPES = ("custodian", "crm", "tax", "payroll", "recordkeeper", "productivity", "filing",
                   "accounting", "government", "other")
_CONNECTOR_DIRECTIONS = ("inbound", "outbound", "bidirectional")
_CONNECTION_STATUSES = ("not_connected", "connected", "error", "disabled")
_CREDENTIAL_TYPES = ("oauth", "api_key", "basic", "certificate", "none")
_REFERENCE_KINDS = ("microsoft_account", "encrypted_secret", "external_vault", "none")
_CREDENTIAL_STATUSES = ("active", "revoked", "expired")
_SYNC_HEALTH = ("healthy", "degraded", "failed", "unknown")
_RUN_STATUSES = ("pending", "running", "succeeded", "failed", "partial")
_RUN_TRIGGERS = ("manual", "automation", "workflow", "api")
_CONFLICT_RESOLUTIONS = ("unresolved", "source_wins", "target_wins", "manual", "skipped")
_WEBHOOK_DIRECTIONS = ("inbound", "outbound")
_SIGNING_ALGORITHMS = ("hmac_sha256", "hmac_sha1", "none")
_VERIFICATION_STATUSES = ("unverified", "verified", "failed")
_DELIVERY_STATUSES = ("pending", "delivered", "failed", "dead")
_API_CLIENT_STATUSES = ("active", "suspended", "revoked")
_PROFILE_TYPES = ("import", "export")
_DATA_FORMATS = ("csv", "json", "xml", "pdf", "xlsx")

# Automation JOB_TYPES (current 16 = 13 base + 3 governance from D.23) widened with integration_sync.
_JOB_TYPES_OLD = ("run_report_schedule", "report_schedules_sweep", "capture_analytics_snapshots",
                  "launch_workflow", "workflow_sla_sweep", "dispatch_notifications", "dispatch_outbox",
                  "send_communication", "m365_mail_sync", "m365_calendar_sync", "m365_document_sync",
                  "governance_quality_scan", "governance_stale_scan", "governance_retention_review",
                  "maintenance", "custom")
_JOB_TYPES_NEW = _JOB_TYPES_OLD + ("integration_sync",)


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def _set_job_type_check(table, constraint, values):
    op.drop_constraint(constraint, table, type_="check")
    op.create_check_constraint(constraint, table, _in("job_type", values))


# (code, name, provider_type)
_PROVIDER_SEED = (
    ("microsoft365", "Microsoft 365", "productivity"),
    ("schwab", "Charles Schwab", "custodian"),
    ("assetmark", "AssetMark", "custodian"),
    ("wealthbox", "Wealthbox", "crm"),
    ("taxdome", "TaxDome", "tax"),
    ("drake", "Drake", "tax"),
    ("adp", "ADP", "payroll"),
    ("guideline", "Guideline", "recordkeeper"),
    ("betterment_401k", "Betterment 401(k)", "recordkeeper"),
    ("quickbooks", "QuickBooks", "accounting"),
    ("irs", "IRS", "government"),
    ("state_efile", "State e-file", "government"),
)

_CAPS = (
    ("integration.view", "View integration providers, connectors, syncs, webhooks, and API clients.", False,
     ("administrator", "operations", "advisor", "compliance")),
    ("integration.manage", "Create and configure providers, connectors, profiles, webhooks, API clients.", False,
     ("administrator", "operations")),
    ("integration.execute", "Run synchronizations, publish events, and record deliveries.", False,
     ("administrator", "operations")),
    ("integration.audit", "View integration audit history and credential-reference metadata.", True,
     ("administrator", "compliance")),
    ("integration.admin", "Administer the integration platform.", True, ("administrator",)),
)


def upgrade():
    bind = op.get_bind()

    op.create_table(
        "integration_providers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("provider_type", sa.Text, nullable=False, server_default="other"),
        sa.Column("category", sa.Text),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("description", sa.Text),
        sa.Column("capabilities", sa.JSON),
        sa.Column("provider_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("provider_type", _PROVIDER_TYPES), name="ck_integration_provider_type"),
    )
    op.create_table(
        "integration_credential_references",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("provider_id", sa.Integer, sa.ForeignKey("integration_providers.id", ondelete="SET NULL")),
        sa.Column("credential_type", sa.Text, nullable=False, server_default="oauth"),
        sa.Column("reference_kind", sa.Text, nullable=False, server_default="microsoft_account"),
        sa.Column("reference_id", sa.Integer),
        sa.Column("secret_ciphertext", sa.Text),
        sa.Column("scopes", sa.JSON),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("rotated_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("credential_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("credential_type", _CREDENTIAL_TYPES), name="ck_integration_credential_type"),
        sa.CheckConstraint(_in("reference_kind", _REFERENCE_KINDS), name="ck_integration_credential_kind"),
        sa.CheckConstraint(_in("status", _CREDENTIAL_STATUSES), name="ck_integration_credential_status"),
    )
    op.create_table(
        "integration_connectors",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("provider_id", sa.Integer, sa.ForeignKey("integration_providers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("direction", sa.Text, nullable=False, server_default="inbound"),
        sa.Column("status", sa.Text, nullable=False, server_default="not_connected"),
        sa.Column("config", sa.JSON),
        sa.Column("credential_reference_id", sa.Integer,
                  sa.ForeignKey("integration_credential_references.id", ondelete="SET NULL")),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("last_status_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
        sa.Column("connector_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("direction", _CONNECTOR_DIRECTIONS), name="ck_integration_connector_direction"),
        sa.CheckConstraint(_in("status", _CONNECTION_STATUSES), name="ck_integration_connector_status"),
    )
    op.create_index("ix_integration_connectors_provider", "integration_connectors", ["provider_id"])

    op.create_table(
        "integration_sync_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("connector_id", sa.Integer, sa.ForeignKey("integration_connectors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("direction", sa.Text, nullable=False, server_default="inbound"),
        sa.Column("entity_types", sa.JSON),
        sa.Column("mapping", sa.JSON),
        sa.Column("mapping_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("transformation", sa.JSON),
        sa.Column("retry_policy_id", sa.Integer),
        sa.Column("failure_policy_id", sa.Integer),
        sa.Column("schedule_frequency", sa.Text),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("next_sync_at", sa.DateTime(timezone=True)),
        sa.Column("sync_health", sa.Text, nullable=False, server_default="unknown"),
        sa.Column("profile_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("direction", _CONNECTOR_DIRECTIONS), name="ck_integration_sync_direction"),
        sa.CheckConstraint(_in("sync_health", _SYNC_HEALTH), name="ck_integration_sync_health"),
    )
    op.create_table(
        "integration_sync_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sync_profile_id", sa.Integer, sa.ForeignKey("integration_sync_profiles.id", ondelete="SET NULL")),
        sa.Column("connector_id", sa.Integer, sa.ForeignKey("integration_connectors.id", ondelete="SET NULL")),
        sa.Column("direction", sa.Text, nullable=False, server_default="inbound"),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("records_read", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_written", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_skipped", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_failed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("import_jobs_id", sa.Integer),
        sa.Column("automation_run_id", sa.Integer),
        sa.Column("microsoft_account_id", sa.Integer),
        sa.Column("trigger_source", sa.Text, nullable=False, server_default="manual"),
        sa.Column("triggered_by_user_id", sa.Integer),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
        sa.Column("run_metadata", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _RUN_STATUSES), name="ck_integration_run_status"),
        sa.CheckConstraint(_in("trigger_source", _RUN_TRIGGERS), name="ck_integration_run_trigger"),
    )
    op.create_index("ix_integration_runs_status", "integration_sync_runs", ["status"])
    op.create_index("ix_integration_runs_profile", "integration_sync_runs", ["sync_profile_id"])

    op.create_table(
        "integration_sync_conflicts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sync_run_id", sa.Integer, sa.ForeignKey("integration_sync_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Integer),
        sa.Column("field_name", sa.Text),
        sa.Column("source_value", sa.Text),
        sa.Column("target_value", sa.Text),
        sa.Column("resolution", sa.Text, nullable=False, server_default="unresolved"),
        sa.Column("resolved_by_user_id", sa.Integer),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("conflict_metadata", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("resolution", _CONFLICT_RESOLUTIONS), name="ck_integration_conflict_resolution"),
    )
    op.create_index("ix_integration_conflicts_run", "integration_sync_conflicts", ["sync_run_id"])

    op.create_table(
        "integration_webhook_endpoints",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("direction", sa.Text, nullable=False, server_default="outbound"),
        sa.Column("url", sa.Text),
        sa.Column("signing_algorithm", sa.Text, nullable=False, server_default="hmac_sha256"),
        sa.Column("signing_secret_ciphertext", sa.Text),
        sa.Column("verification_status", sa.Text, nullable=False, server_default="unverified"),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("endpoint_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("direction", _WEBHOOK_DIRECTIONS), name="ck_integration_webhook_direction"),
        sa.CheckConstraint(_in("signing_algorithm", _SIGNING_ALGORITHMS), name="ck_integration_webhook_signing"),
        sa.CheckConstraint(_in("verification_status", _VERIFICATION_STATUSES), name="ck_integration_webhook_verify"),
    )
    op.create_table(
        "integration_webhook_subscriptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("endpoint_id", sa.Integer, sa.ForeignKey("integration_webhook_endpoints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("filter", sa.JSON),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("endpoint_id", "event_type", name="uq_integration_webhook_subscription"),
    )
    op.create_table(
        "integration_webhook_deliveries",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("subscription_id", sa.Integer, sa.ForeignKey("integration_webhook_subscriptions.id", ondelete="SET NULL")),
        sa.Column("endpoint_id", sa.Integer, sa.ForeignKey("integration_webhook_endpoints.id", ondelete="SET NULL")),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("event_id", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="5"),
        sa.Column("available_at", sa.DateTime(timezone=True)),
        sa.Column("response_code", sa.Integer),
        sa.Column("signature", sa.Text),
        sa.Column("last_error", sa.Text),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("delivery_metadata", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _DELIVERY_STATUSES), name="ck_integration_delivery_status"),
    )
    op.create_index("ix_integration_deliveries_endpoint", "integration_webhook_deliveries", ["endpoint_id"])

    op.create_table(
        "integration_api_clients",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("client_type", sa.Text, nullable=False, server_default="internal"),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("scopes", sa.JSON),
        sa.Column("credential_reference_id", sa.Integer,
                  sa.ForeignKey("integration_credential_references.id", ondelete="SET NULL")),
        sa.Column("rate_limit_per_minute", sa.Integer),
        sa.Column("rate_limit_per_day", sa.Integer),
        sa.Column("description", sa.Text),
        sa.Column("client_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _API_CLIENT_STATUSES), name="ck_integration_api_client_status"),
    )
    op.create_table(
        "integration_api_usage",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("api_client_id", sa.Integer, sa.ForeignKey("integration_api_clients.id", ondelete="SET NULL")),
        sa.Column("endpoint", sa.Text),
        sa.Column("method", sa.Text),
        sa.Column("request_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("window_start", sa.DateTime(timezone=True)),
        sa.Column("window_end", sa.DateTime(timezone=True)),
        sa.Column("usage_metadata", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_integration_api_usage_client", "integration_api_usage", ["api_client_id"])

    op.create_table(
        "integration_event_definitions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("category", sa.Text),
        sa.Column("payload_schema", sa.JSON),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "integration_event_subscriptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_definition_id", sa.Integer, sa.ForeignKey("integration_event_definitions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subscriber", sa.Text, nullable=False),
        sa.Column("subscriber_type", sa.Text, nullable=False, server_default="internal"),
        sa.Column("target_id", sa.Integer),
        sa.Column("filter", sa.JSON),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "integration_data_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("profile_type", sa.Text, nullable=False, server_default="import"),
        sa.Column("provider_id", sa.Integer, sa.ForeignKey("integration_providers.id", ondelete="SET NULL")),
        sa.Column("data_format", sa.Text, nullable=False, server_default="csv"),
        sa.Column("mapping", sa.JSON),
        sa.Column("transformation", sa.JSON),
        sa.Column("delivery", sa.Text),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("description", sa.Text),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("profile_type", _PROFILE_TYPES), name="ck_integration_data_profile_type"),
        sa.CheckConstraint(_in("data_format", _DATA_FORMATS), name="ck_integration_data_profile_format"),
    )

    # Append-only audit ledger (polymorphic; no FK so parent deletes never touch immutable rows).
    op.create_table(
        "integration_events",
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
    op.create_index("ix_integration_events_entity", "integration_events", ["entity_type", "entity_id"])
    op.execute(
        "CREATE OR REPLACE FUNCTION prevent_integration_event_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'integration_events are append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER integration_events_immutable BEFORE UPDATE OR DELETE ON integration_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_integration_event_mutation()"
    )

    # Widen the Automation JOB_TYPES CHECKs so Automation may run integration syncs (D.22 reuse).
    _set_job_type_check("automation_jobs", "ck_automation_job_type", _JOB_TYPES_NEW)
    _set_job_type_check("automation_job_templates", "ck_automation_template_job_type", _JOB_TYPES_NEW)

    # Seed the known providers, disabled by default (never activates provider logic).
    for code, name, ptype in _PROVIDER_SEED:
        if bind.execute(sa.text("SELECT id FROM integration_providers WHERE code=:c"), {"c": code}).scalar() is None:
            bind.execute(sa.text(
                "INSERT INTO integration_providers (code, name, provider_type, enabled) "
                "VALUES (:c, :n, :t, false)"), {"c": code, "n": name, "t": ptype})

    # Seed two event definitions (integration lifecycle events published through the outbox).
    for code, name, category in (("integration.sync.completed", "Sync completed", "sync"),
                                 ("integration.sync.failed", "Sync failed", "sync")):
        if bind.execute(sa.text("SELECT id FROM integration_event_definitions WHERE code=:c"), {"c": code}).scalar() is None:
            bind.execute(sa.text(
                "INSERT INTO integration_event_definitions (code, name, category, active) "
                "VALUES (:c, :n, :cat, true)"), {"c": code, "n": name, "cat": category})

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

    op.execute("DROP TRIGGER IF EXISTS integration_events_immutable ON integration_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_integration_event_mutation()")
    op.drop_table("integration_events")
    op.drop_table("integration_data_profiles")
    op.drop_table("integration_event_subscriptions")
    op.drop_table("integration_event_definitions")
    op.drop_table("integration_api_usage")
    op.drop_table("integration_api_clients")
    op.drop_table("integration_webhook_deliveries")
    op.drop_table("integration_webhook_subscriptions")
    op.drop_table("integration_webhook_endpoints")
    op.drop_table("integration_sync_conflicts")
    op.drop_table("integration_sync_runs")
    op.drop_table("integration_sync_profiles")
    op.drop_table("integration_connectors")
    op.drop_table("integration_credential_references")
    op.drop_table("integration_providers")
