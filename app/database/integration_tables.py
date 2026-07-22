"""Declared schema for the Phase D.24 Enterprise Integration platform.

Mirrors the live schema created by migration ``v2a3b4c5d6e7``. Integration is a new authoritative
INTEGRATION domain that owns **integration metadata only** — providers, connectors
(instance/config/status), credential references, synchronization profiles/runs/conflicts, webhook
endpoints/subscriptions/deliveries, API clients/usage/rate-limits, event definitions/subscriptions,
and import/export profiles — plus an append-only audit ledger. It **owns no business records** and
is **never a source of truth**. It **reuses** the existing importers (``import_jobs``), Microsoft 365
OAuth/sync-health (``microsoft_accounts``), the Fernet encryption helpers, the transactional
**outbox** as the event bus, and the automation dispatch registry — never duplicating provider
logic, **never storing a plaintext secret** (credential/webhook secrets are pointers or Fernet
ciphertext), and **no external broker**. Webhook delivery is metadata only (no outbound HTTP this
phase). ``integration_events`` is the append-only audit ledger (trigger-blocked BEFORE UPDATE OR
DELETE).
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
    UniqueConstraint,
    func,
)

# Deterministic controlled vocabularies (metadata only).
PROVIDER_TYPES = ("custodian", "crm", "tax", "payroll", "recordkeeper", "productivity", "filing",
                  "accounting", "government", "other")
CONNECTOR_DIRECTIONS = ("inbound", "outbound", "bidirectional")
CONNECTION_STATUSES = ("not_connected", "connected", "error", "disabled")
CREDENTIAL_TYPES = ("oauth", "api_key", "basic", "certificate", "none")
REFERENCE_KINDS = ("microsoft_account", "encrypted_secret", "external_vault", "none")
CREDENTIAL_STATUSES = ("active", "revoked", "expired")
SYNC_HEALTH = ("healthy", "degraded", "failed", "unknown")
RUN_STATUSES = ("pending", "running", "succeeded", "failed", "partial")
RUN_TRIGGERS = ("manual", "automation", "workflow", "api")
CONFLICT_RESOLUTIONS = ("unresolved", "source_wins", "target_wins", "manual", "skipped")
WEBHOOK_DIRECTIONS = ("inbound", "outbound")
SIGNING_ALGORITHMS = ("hmac_sha256", "hmac_sha1", "none")
VERIFICATION_STATUSES = ("unverified", "verified", "failed")
DELIVERY_STATUSES = ("pending", "delivered", "failed", "dead")
API_CLIENT_STATUSES = ("active", "suspended", "revoked")
PROFILE_TYPES = ("import", "export")
DATA_FORMATS = ("csv", "json", "xml", "pdf", "xlsx")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def define_integration_tables(metadata: MetaData):
    providers = Table(
        "integration_providers", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("provider_type", Text, nullable=False, server_default="other"),
        Column("category", Text),
        Column("enabled", Boolean, nullable=False, server_default="false"),   # disabled-by-default
        Column("description", Text),
        Column("capabilities", JSON),
        Column("provider_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("provider_type", PROVIDER_TYPES), name="ck_integration_provider_type"),
    )
    credentials = Table(
        "integration_credential_references", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("provider_id", Integer, ForeignKey("integration_providers.id", ondelete="SET NULL")),
        Column("credential_type", Text, nullable=False, server_default="oauth"),
        Column("reference_kind", Text, nullable=False, server_default="microsoft_account"),
        Column("reference_id", Integer),          # e.g. microsoft_accounts.id (existing enc store)
        Column("secret_ciphertext", Text),        # Fernet only — NEVER plaintext (optional)
        Column("scopes", JSON),
        Column("status", Text, nullable=False, server_default="active"),
        Column("rotated_at", DateTime(timezone=True)),
        Column("expires_at", DateTime(timezone=True)),
        Column("credential_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("credential_type", CREDENTIAL_TYPES), name="ck_integration_credential_type"),
        CheckConstraint(_in("reference_kind", REFERENCE_KINDS), name="ck_integration_credential_kind"),
        CheckConstraint(_in("status", CREDENTIAL_STATUSES), name="ck_integration_credential_status"),
    )
    connectors = Table(
        "integration_connectors", metadata,
        Column("id", Integer, primary_key=True),
        Column("provider_id", Integer,
               ForeignKey("integration_providers.id", ondelete="CASCADE"), nullable=False),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("direction", Text, nullable=False, server_default="inbound"),
        Column("status", Text, nullable=False, server_default="not_connected"),
        Column("config", JSON),                   # connector configuration — NO secrets
        Column("credential_reference_id", Integer,
               ForeignKey("integration_credential_references.id", ondelete="SET NULL")),
        Column("enabled", Boolean, nullable=False, server_default="false"),
        Column("last_status_at", DateTime(timezone=True)),
        Column("last_error", Text),
        Column("connector_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("direction", CONNECTOR_DIRECTIONS), name="ck_integration_connector_direction"),
        CheckConstraint(_in("status", CONNECTION_STATUSES), name="ck_integration_connector_status"),
    )
    sync_profiles = Table(
        "integration_sync_profiles", metadata,
        Column("id", Integer, primary_key=True),
        Column("connector_id", Integer,
               ForeignKey("integration_connectors.id", ondelete="CASCADE"), nullable=False),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("direction", Text, nullable=False, server_default="inbound"),
        Column("entity_types", JSON),
        Column("mapping", JSON),                  # synchronization mapping
        Column("mapping_version", Integer, nullable=False, server_default="1"),
        Column("transformation", JSON),           # embedded transformation profile
        Column("retry_policy_id", Integer),       # reference automation_retry_policies (plain)
        Column("failure_policy_id", Integer),     # reference automation_failure_policies (plain)
        Column("schedule_frequency", Text),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("last_sync_at", DateTime(timezone=True)),
        Column("next_sync_at", DateTime(timezone=True)),
        Column("sync_health", Text, nullable=False, server_default="unknown"),
        Column("profile_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("direction", CONNECTOR_DIRECTIONS), name="ck_integration_sync_direction"),
        CheckConstraint(_in("sync_health", SYNC_HEALTH), name="ck_integration_sync_health"),
    )
    sync_runs = Table(
        "integration_sync_runs", metadata,
        Column("id", Integer, primary_key=True),
        Column("sync_profile_id", Integer,
               ForeignKey("integration_sync_profiles.id", ondelete="SET NULL")),
        Column("connector_id", Integer, ForeignKey("integration_connectors.id", ondelete="SET NULL")),
        Column("direction", Text, nullable=False, server_default="inbound"),
        Column("status", Text, nullable=False, server_default="pending"),
        Column("records_read", Integer, nullable=False, server_default="0"),
        Column("records_written", Integer, nullable=False, server_default="0"),
        Column("records_skipped", Integer, nullable=False, server_default="0"),
        Column("records_failed", Integer, nullable=False, server_default="0"),
        # References to the EXISTING run ledgers (never replaced).
        Column("import_jobs_id", Integer),
        Column("automation_run_id", Integer),
        Column("microsoft_account_id", Integer),
        Column("trigger_source", Text, nullable=False, server_default="manual"),
        Column("triggered_by_user_id", Integer),
        # Optional client anchor (a client-scoped sync) for guarded timeline publication.
        Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("household_id", Integer, ForeignKey("households.id", ondelete="SET NULL")),
        Column("started_at", DateTime(timezone=True)),
        Column("finished_at", DateTime(timezone=True)),
        Column("last_error", Text),
        Column("run_metadata", JSON),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", RUN_STATUSES), name="ck_integration_run_status"),
        CheckConstraint(_in("trigger_source", RUN_TRIGGERS), name="ck_integration_run_trigger"),
    )
    conflicts = Table(
        "integration_sync_conflicts", metadata,
        Column("id", Integer, primary_key=True),
        Column("sync_run_id", Integer,
               ForeignKey("integration_sync_runs.id", ondelete="CASCADE"), nullable=False),
        Column("entity_type", Text, nullable=False),
        Column("entity_id", Integer),
        Column("field_name", Text),
        Column("source_value", Text),
        Column("target_value", Text),
        Column("resolution", Text, nullable=False, server_default="unresolved"),
        Column("resolved_by_user_id", Integer),
        Column("resolved_at", DateTime(timezone=True)),
        Column("conflict_metadata", JSON),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("resolution", CONFLICT_RESOLUTIONS), name="ck_integration_conflict_resolution"),
    )
    webhook_endpoints = Table(
        "integration_webhook_endpoints", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("direction", Text, nullable=False, server_default="outbound"),
        Column("url", Text),
        Column("signing_algorithm", Text, nullable=False, server_default="hmac_sha256"),
        Column("signing_secret_ciphertext", Text),   # Fernet only — NEVER plaintext
        Column("verification_status", Text, nullable=False, server_default="unverified"),
        Column("verified_at", DateTime(timezone=True)),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("endpoint_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("direction", WEBHOOK_DIRECTIONS), name="ck_integration_webhook_direction"),
        CheckConstraint(_in("signing_algorithm", SIGNING_ALGORITHMS), name="ck_integration_webhook_signing"),
        CheckConstraint(_in("verification_status", VERIFICATION_STATUSES), name="ck_integration_webhook_verify"),
    )
    webhook_subscriptions = Table(
        "integration_webhook_subscriptions", metadata,
        Column("id", Integer, primary_key=True),
        Column("endpoint_id", Integer,
               ForeignKey("integration_webhook_endpoints.id", ondelete="CASCADE"), nullable=False),
        Column("event_type", Text, nullable=False),
        Column("filter", JSON),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("endpoint_id", "event_type", name="uq_integration_webhook_subscription"),
    )
    webhook_deliveries = Table(
        "integration_webhook_deliveries", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("subscription_id", Integer,
               ForeignKey("integration_webhook_subscriptions.id", ondelete="SET NULL")),
        Column("endpoint_id", Integer,
               ForeignKey("integration_webhook_endpoints.id", ondelete="SET NULL")),
        Column("event_type", Text, nullable=False),
        Column("event_id", Text),                 # references the outbox event id
        Column("status", Text, nullable=False, server_default="pending"),
        Column("attempts", Integer, nullable=False, server_default="0"),
        Column("max_attempts", Integer, nullable=False, server_default="5"),
        Column("available_at", DateTime(timezone=True)),
        Column("response_code", Integer),
        Column("signature", Text),                # computed HMAC signature (metadata; no plaintext secret)
        Column("last_error", Text),
        Column("delivered_at", DateTime(timezone=True)),
        Column("delivery_metadata", JSON),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", DELIVERY_STATUSES), name="ck_integration_delivery_status"),
    )
    api_clients = Table(
        "integration_api_clients", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("client_type", Text, nullable=False, server_default="internal"),
        Column("status", Text, nullable=False, server_default="active"),
        Column("scopes", JSON),
        Column("credential_reference_id", Integer,
               ForeignKey("integration_credential_references.id", ondelete="SET NULL")),
        Column("rate_limit_per_minute", Integer),
        Column("rate_limit_per_day", Integer),
        Column("description", Text),
        Column("client_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", API_CLIENT_STATUSES), name="ck_integration_api_client_status"),
    )
    api_usage = Table(
        "integration_api_usage", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("api_client_id", Integer,
               ForeignKey("integration_api_clients.id", ondelete="SET NULL")),
        Column("endpoint", Text),
        Column("method", Text),
        Column("request_count", Integer, nullable=False, server_default="0"),
        Column("error_count", Integer, nullable=False, server_default="0"),
        Column("window_start", DateTime(timezone=True)),
        Column("window_end", DateTime(timezone=True)),
        Column("usage_metadata", JSON),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    event_definitions = Table(
        "integration_event_definitions", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),      # the outbox event_type
        Column("name", Text, nullable=False),
        Column("description", Text),
        Column("category", Text),
        Column("payload_schema", JSON),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    event_subscriptions = Table(
        "integration_event_subscriptions", metadata,
        Column("id", Integer, primary_key=True),
        Column("event_definition_id", Integer,
               ForeignKey("integration_event_definitions.id", ondelete="CASCADE"), nullable=False),
        Column("subscriber", Text, nullable=False),
        Column("subscriber_type", Text, nullable=False, server_default="internal"),
        Column("target_id", Integer),             # e.g. a webhook endpoint id
        Column("filter", JSON),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    data_profiles = Table(
        "integration_data_profiles", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("profile_type", Text, nullable=False, server_default="import"),
        Column("provider_id", Integer, ForeignKey("integration_providers.id", ondelete="SET NULL")),
        Column("data_format", Text, nullable=False, server_default="csv"),
        Column("mapping", JSON),
        Column("transformation", JSON),
        Column("delivery", Text),                 # for exports: download|webhook|api
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("description", Text),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("profile_type", PROFILE_TYPES), name="ck_integration_data_profile_type"),
        CheckConstraint(_in("data_format", DATA_FORMATS), name="ck_integration_data_profile_format"),
    )
    # Append-only audit ledger (polymorphic; no FK so parent deletes never touch immutable rows).
    events = Table(
        "integration_events", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("entity_type", Text, nullable=False),   # provider | connector | sync_run | webhook ...
        Column("entity_id", Integer, nullable=False),
        Column("event_type", Text, nullable=False),
        Column("from_status", Text),
        Column("to_status", Text),
        Column("actor_user_id", Integer),
        Column("payload", JSON),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    return {
        "integration_providers": providers,
        "integration_credential_references": credentials,
        "integration_connectors": connectors,
        "integration_sync_profiles": sync_profiles,
        "integration_sync_runs": sync_runs,
        "integration_sync_conflicts": conflicts,
        "integration_webhook_endpoints": webhook_endpoints,
        "integration_webhook_subscriptions": webhook_subscriptions,
        "integration_webhook_deliveries": webhook_deliveries,
        "integration_api_clients": api_clients,
        "integration_api_usage": api_usage,
        "integration_event_definitions": event_definitions,
        "integration_event_subscriptions": event_subscriptions,
        "integration_data_profiles": data_profiles,
        "integration_events": events,
    }
