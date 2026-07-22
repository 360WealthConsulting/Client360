"""Declared schema for the Phase D.27 Enterprise Configuration platform.

Mirrors the live schema created by migration ``y9c0d1e2f3a4``. Enterprise Configuration is a new
authoritative PLATFORM-CONFIGURATION domain that owns **configuration governance metadata only** —
configuration categories/sets/items/versions, environment overrides, tenant/organization/user
preferences, feature groups/flags/rollouts, editions/edition-capabilities/license-policies/edition-
assignments, platform options, administrative policies, runtime-setting references, configuration
snapshots, and configuration changes — plus an append-only audit ledger (``configuration_events``).
It **owns no business records** and is **never the source of truth** for operational or business
entities.

It **references** Security/Observability/Integration/Automation/Reporting/Analytics/Workflow/
Communications/Microsoft 365/Timeline/Audit and **reuses** the existing runtime configuration
(``app/config.py``), the environment-variable infrastructure, and the RBAC ``capabilities`` — it
**never replaces** the runtime configuration/env loaders (runtime-setting references only *point at*
``app.config`` functions/env vars), **never owns** organizations/users (it references
``organization_profiles.id``/``users.id``), and stores no secrets. ``configuration_events`` is the
append-only audit ledger (trigger-blocked BEFORE UPDATE OR DELETE).
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
SET_STATUSES = ("draft", "active", "approved", "archived")
ITEM_STATUSES = ("draft", "active", "approved", "archived")
VALUE_TYPES = ("string", "integer", "boolean", "float", "json")
ENVIRONMENTS = ("production", "staging", "development", "test", "all")
PREFERENCE_SCOPES = ("tenant", "organization", "user")
FEATURE_STATUSES = ("draft", "active", "deprecated", "archived")
ROLLOUT_STATUSES = ("planned", "active", "paused", "completed", "rolled_back")
EDITION_TIERS = ("free", "standard", "professional", "enterprise")
EDITION_STATUSES = ("draft", "active", "retired")
LICENSE_STATUSES = ("active", "expired", "suspended")
ASSIGNMENT_STATUSES = ("active", "suspended", "revoked")
ADMIN_POLICY_STATUSES = ("draft", "active", "approved", "archived")
CHANGE_TYPES = ("create", "update", "delete", "override")
CHANGE_STATUSES = ("proposed", "approved", "applied", "rejected")
OPTION_TYPES = ("boolean", "string", "integer", "json", "enum")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def define_configuration_tables(metadata: MetaData):
    # --- configuration hierarchy: category -> set -> item -> version --------------------------
    categories = Table(
        "configuration_categories", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("description", Text),
        Column("sort_order", Integer, nullable=False, server_default="0"),
        Column("category_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    sets = Table(
        "configuration_sets", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("category_id", Integer, ForeignKey("configuration_categories.id", ondelete="SET NULL")),
        Column("status", Text, nullable=False, server_default="draft"),
        Column("description", Text),
        Column("set_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", SET_STATUSES), name="ck_configuration_set_status"),
    )
    items = Table(
        "configuration_items", metadata,
        Column("id", Integer, primary_key=True),
        Column("set_id", Integer, ForeignKey("configuration_sets.id", ondelete="CASCADE"), nullable=False),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("value_type", Text, nullable=False, server_default="string"),
        Column("value", JSON),
        Column("default_value", JSON),
        Column("status", Text, nullable=False, server_default="draft"),
        Column("version", Integer, nullable=False, server_default="1"),
        Column("sensitive", Boolean, nullable=False, server_default="false"),
        # Governance pointer at an existing runtime setting (app.config function / env) — never owns it.
        Column("runtime_setting_reference", Text),
        Column("description", Text),
        Column("item_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("value_type", VALUE_TYPES), name="ck_configuration_item_value_type"),
        CheckConstraint(_in("status", ITEM_STATUSES), name="ck_configuration_item_status"),
    )
    versions = Table(
        "configuration_versions", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("configuration_item_id", Integer,
               ForeignKey("configuration_items.id", ondelete="CASCADE"), nullable=False),
        Column("version", Integer, nullable=False),
        Column("value", JSON),
        Column("note", Text),
        Column("changed_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("configuration_item_id", "version", name="uq_configuration_version"),
    )
    environment_overrides = Table(
        "configuration_environment_overrides", metadata,
        Column("id", Integer, primary_key=True),
        Column("configuration_item_id", Integer,
               ForeignKey("configuration_items.id", ondelete="CASCADE"), nullable=False),
        Column("environment", Text, nullable=False, server_default="production"),
        Column("value", JSON),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("note", Text),
        Column("override_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("environment", ENVIRONMENTS), name="ck_configuration_override_environment"),
        UniqueConstraint("configuration_item_id", "environment", name="uq_configuration_override"),
    )
    # --- preferences (tenant / organization / user via scope) ----------------------------------
    preferences = Table(
        "configuration_preferences", metadata,
        Column("id", Integer, primary_key=True),
        Column("scope", Text, nullable=False, server_default="tenant"),
        # Optional scope anchor: organization_profiles.id (org scope) or users.id (user scope).
        Column("organization_id", Integer, ForeignKey("organization_profiles.id", ondelete="SET NULL")),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("preference_key", Text, nullable=False),
        Column("value", JSON),
        # For user-preference references: a pointer to where the real preference lives (never owns it).
        Column("reference", Text),
        Column("description", Text),
        Column("preference_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("scope", PREFERENCE_SCOPES), name="ck_configuration_preference_scope"),
        UniqueConstraint("scope", "organization_id", "user_id", "preference_key",
                         name="uq_configuration_preference"),
    )
    # --- feature management ---------------------------------------------------------------------
    feature_groups = Table(
        "configuration_feature_groups", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("description", Text),
        Column("group_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    feature_flags = Table(
        "configuration_feature_flags", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("feature_group_id", Integer,
               ForeignKey("configuration_feature_groups.id", ondelete="SET NULL")),
        Column("status", Text, nullable=False, server_default="draft"),
        Column("enabled", Boolean, nullable=False, server_default="false"),  # governance intent only
        Column("rollout_percentage", Integer, nullable=False, server_default="0"),
        Column("target_roles", JSON),
        Column("target_organizations", JSON),
        Column("activation_starts_at", DateTime(timezone=True)),
        Column("activation_ends_at", DateTime(timezone=True)),
        Column("deprecation_at", DateTime(timezone=True)),
        Column("replacement_feature_id", Integer),   # references another flag (governance, no FK)
        # Governance pointer at an existing runtime toggle (app.config function) — never owns it.
        Column("runtime_setting_reference", Text),
        Column("description", Text),
        Column("flag_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", FEATURE_STATUSES), name="ck_configuration_feature_status"),
        CheckConstraint("rollout_percentage >= 0 AND rollout_percentage <= 100",
                        name="ck_configuration_feature_rollout_pct"),
    )
    feature_rollouts = Table(
        "configuration_feature_rollouts", metadata,
        Column("id", Integer, primary_key=True),
        Column("feature_flag_id", Integer,
               ForeignKey("configuration_feature_flags.id", ondelete="CASCADE"), nullable=False),
        Column("stage", Text, nullable=False),
        Column("percentage", Integer, nullable=False, server_default="0"),
        Column("status", Text, nullable=False, server_default="planned"),
        Column("starts_at", DateTime(timezone=True)),
        Column("ends_at", DateTime(timezone=True)),
        Column("note", Text),
        Column("rollout_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", ROLLOUT_STATUSES), name="ck_configuration_rollout_status"),
        CheckConstraint("percentage >= 0 AND percentage <= 100", name="ck_configuration_rollout_pct"),
    )
    # --- editions / licensing ------------------------------------------------------------------
    editions = Table(
        "configuration_editions", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("tier", Text, nullable=False, server_default="standard"),
        Column("status", Text, nullable=False, server_default="draft"),
        Column("description", Text),
        Column("edition_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("tier", EDITION_TIERS), name="ck_configuration_edition_tier"),
        CheckConstraint(_in("status", EDITION_STATUSES), name="ck_configuration_edition_status"),
    )
    edition_capabilities = Table(
        "configuration_edition_capabilities", metadata,
        Column("id", Integer, primary_key=True),
        Column("edition_id", Integer,
               ForeignKey("configuration_editions.id", ondelete="CASCADE"), nullable=False),
        # References the existing RBAC capabilities.code (unique) — never duplicates a capability.
        Column("capability_code", Text, nullable=False),
        Column("included", Boolean, nullable=False, server_default="true"),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("edition_id", "capability_code", name="uq_configuration_edition_capability"),
    )
    license_policies = Table(
        "configuration_license_policies", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("edition_id", Integer, ForeignKey("configuration_editions.id", ondelete="SET NULL")),
        Column("max_users", Integer),
        Column("max_organizations", Integer),
        Column("features", JSON),
        Column("status", Text, nullable=False, server_default="active"),
        Column("effective_at", DateTime(timezone=True)),
        Column("expires_at", DateTime(timezone=True)),
        Column("policy_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", LICENSE_STATUSES), name="ck_configuration_license_status"),
    )
    edition_assignments = Table(
        "configuration_edition_assignments", metadata,
        Column("id", Integer, primary_key=True),
        Column("edition_id", Integer, ForeignKey("configuration_editions.id", ondelete="SET NULL")),
        Column("license_policy_id", Integer,
               ForeignKey("configuration_license_policies.id", ondelete="SET NULL")),
        Column("scope", Text, nullable=False, server_default="tenant"),
        Column("organization_id", Integer, ForeignKey("organization_profiles.id", ondelete="SET NULL")),
        Column("status", Text, nullable=False, server_default="active"),
        Column("assigned_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("expires_at", DateTime(timezone=True)),
        Column("assignment_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("scope", PREFERENCE_SCOPES), name="ck_configuration_assignment_scope"),
        CheckConstraint(_in("status", ASSIGNMENT_STATUSES), name="ck_configuration_assignment_status"),
    )
    # --- platform options / administrative policies / runtime references -----------------------
    platform_options = Table(
        "configuration_platform_options", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("option_type", Text, nullable=False, server_default="boolean"),
        Column("value", JSON),
        Column("category", Text),
        Column("editable", Boolean, nullable=False, server_default="true"),
        Column("description", Text),
        Column("option_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("option_type", OPTION_TYPES), name="ck_configuration_option_type"),
    )
    administrative_policies = Table(
        "configuration_administrative_policies", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("policy_type", Text),
        Column("status", Text, nullable=False, server_default="draft"),
        Column("config", JSON),
        Column("approved_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("approved_at", DateTime(timezone=True)),
        Column("description", Text),
        Column("policy_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", ADMIN_POLICY_STATUSES), name="ck_configuration_admin_policy_status"),
    )
    runtime_setting_references = Table(
        "configuration_runtime_setting_references", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("env_var", Text),                 # the env var the runtime setting reads (reference)
        Column("loader_reference", Text),        # e.g. "app.config.automation_enabled" (reference)
        Column("value_type", Text, nullable=False, server_default="string"),
        Column("current_value_note", Text),      # human note; NEVER a secret value
        Column("sensitive", Boolean, nullable=False, server_default="false"),
        Column("description", Text),
        Column("reference_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("value_type", VALUE_TYPES), name="ck_configuration_runtime_value_type"),
    )
    # --- snapshots / changes -------------------------------------------------------------------
    snapshots = Table(
        "configuration_snapshots", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("scope", Text),
        Column("captured_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("payload", JSON),                 # point-in-time capture of config metadata
        Column("summary", Text),
        Column("snapshot_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    )
    changes = Table(
        "configuration_changes", metadata,
        Column("id", Integer, primary_key=True),
        Column("entity_type", Text, nullable=False),   # item | feature_flag | edition | option ...
        Column("entity_id", Integer),
        Column("change_type", Text, nullable=False, server_default="update"),
        Column("from_value", JSON),
        Column("to_value", JSON),
        Column("status", Text, nullable=False, server_default="proposed"),
        Column("requested_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("approved_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("approved_at", DateTime(timezone=True)),
        Column("applied_at", DateTime(timezone=True)),
        Column("note", Text),
        Column("change_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("change_type", CHANGE_TYPES), name="ck_configuration_change_type"),
        CheckConstraint(_in("status", CHANGE_STATUSES), name="ck_configuration_change_status"),
    )
    # --- append-only audit ledger (polymorphic; no FK so parent deletes never touch it) --------
    events = Table(
        "configuration_events", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("entity_type", Text, nullable=False),   # item | feature_flag | edition | change ...
        Column("entity_id", Integer, nullable=False),
        Column("event_type", Text, nullable=False),
        Column("from_status", Text),
        Column("to_status", Text),
        Column("actor_user_id", Integer),
        Column("payload", JSON),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    return {
        "configuration_categories": categories,
        "configuration_sets": sets,
        "configuration_items": items,
        "configuration_versions": versions,
        "configuration_environment_overrides": environment_overrides,
        "configuration_preferences": preferences,
        "configuration_feature_groups": feature_groups,
        "configuration_feature_flags": feature_flags,
        "configuration_feature_rollouts": feature_rollouts,
        "configuration_editions": editions,
        "configuration_edition_capabilities": edition_capabilities,
        "configuration_license_policies": license_policies,
        "configuration_edition_assignments": edition_assignments,
        "configuration_platform_options": platform_options,
        "configuration_administrative_policies": administrative_policies,
        "configuration_runtime_setting_references": runtime_setting_references,
        "configuration_snapshots": snapshots,
        "configuration_changes": changes,
        "configuration_events": events,
    }
