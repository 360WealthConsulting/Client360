"""Enterprise Configuration platform (Phase D.27).

Enterprise Configuration is a new authoritative PLATFORM-CONFIGURATION domain that owns configuration
governance metadata only — categories/sets/items/versions, environment overrides, tenant/organization/
user preferences, feature groups/flags/rollouts, editions/edition-capabilities/license-policies/
edition-assignments, platform options, administrative policies, runtime-setting references, snapshots,
and changes — plus an append-only audit ledger. It **owns no business records**, **references**
Security/Observability/Integration/Automation/Analytics/Timeline/Audit and the RBAC ``capabilities``,
and **reuses** the existing runtime configuration (``app/config.py``) + environment-variable infra
(runtime-setting references only *point at* them). It **never replaces** the runtime config/env
loaders and **never owns** organizations/users (it references ``organization_profiles.id``/
``users.id``).

Tables (19). Also **widens the Automation JOB_TYPES CHECK constraints** to add a
``configuration_review`` job type (so Automation may run configuration validation / drift review /
feature-rollout review). Seeds 5 configuration.* capabilities and a small category registry. Additive
and reversible. Single Alembic head (down ``x8b9c0d1e2f3``).
"""
import sqlalchemy as sa
from alembic import op

revision = "y9c0d1e2f3a4"
down_revision = "x8b9c0d1e2f3"
branch_labels = None
depends_on = None

_SET_STATUSES = ("draft", "active", "approved", "archived")
_ITEM_STATUSES = ("draft", "active", "approved", "archived")
_VALUE_TYPES = ("string", "integer", "boolean", "float", "json")
_ENVIRONMENTS = ("production", "staging", "development", "test", "all")
_PREFERENCE_SCOPES = ("tenant", "organization", "user")
_FEATURE_STATUSES = ("draft", "active", "deprecated", "archived")
_ROLLOUT_STATUSES = ("planned", "active", "paused", "completed", "rolled_back")
_EDITION_TIERS = ("free", "standard", "professional", "enterprise")
_EDITION_STATUSES = ("draft", "active", "retired")
_LICENSE_STATUSES = ("active", "expired", "suspended")
_ASSIGNMENT_STATUSES = ("active", "suspended", "revoked")
_ADMIN_POLICY_STATUSES = ("draft", "active", "approved", "archived")
_CHANGE_TYPES = ("create", "update", "delete", "override")
_CHANGE_STATUSES = ("proposed", "approved", "applied", "rejected")
_OPTION_TYPES = ("boolean", "string", "integer", "json", "enum")

# Automation JOB_TYPES (current 19 = base + governance + integration_sync + security_review +
# observability_scan) widened with configuration_review.
_JOB_TYPES_OLD = ("run_report_schedule", "report_schedules_sweep", "capture_analytics_snapshots",
                  "launch_workflow", "workflow_sla_sweep", "dispatch_notifications", "dispatch_outbox",
                  "send_communication", "m365_mail_sync", "m365_calendar_sync", "m365_document_sync",
                  "governance_quality_scan", "governance_stale_scan", "governance_retention_review",
                  "integration_sync", "security_review", "observability_scan", "maintenance", "custom")
_JOB_TYPES_NEW = _JOB_TYPES_OLD + ("configuration_review",)


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def _set_job_type_check(table, constraint, values):
    op.drop_constraint(constraint, table, type_="check")
    op.create_check_constraint(constraint, table, _in("job_type", values))


_CAPS = (
    ("configuration.view", "View configuration categories/sets/items, preferences, feature flags, "
     "editions, and platform options.", False, ("administrator", "operations", "compliance")),
    ("configuration.manage", "Create and configure categories/sets/items, overrides, preferences, "
     "features, editions, license policies, and platform options.", False,
     ("administrator", "operations")),
    ("configuration.execute", "Approve configuration changes, activate features, assign editions, and "
     "run configuration reviews.", False, ("administrator", "operations")),
    ("configuration.audit", "View configuration audit history and sensitive configuration metadata.",
     True, ("administrator", "compliance")),
    ("configuration.admin", "Administer the configuration platform.", True, ("administrator",)),
)

# (code, name, sort_order) — seed a small configuration-category registry.
_CATEGORY_SEED = (
    ("platform", "Platform", 10),
    ("security", "Security", 20),
    ("integration", "Integration", 30),
    ("automation", "Automation", 40),
    ("features", "Features", 50),
    ("licensing", "Licensing", 60),
)


def upgrade():
    bind = op.get_bind()

    op.create_table(
        "configuration_categories",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("category_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "configuration_sets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("category_id", sa.Integer, sa.ForeignKey("configuration_categories.id", ondelete="SET NULL")),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("description", sa.Text),
        sa.Column("set_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _SET_STATUSES), name="ck_configuration_set_status"),
    )
    op.create_table(
        "configuration_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("set_id", sa.Integer, sa.ForeignKey("configuration_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("value_type", sa.Text, nullable=False, server_default="string"),
        sa.Column("value", sa.JSON),
        sa.Column("default_value", sa.JSON),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("sensitive", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("runtime_setting_reference", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("item_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("value_type", _VALUE_TYPES), name="ck_configuration_item_value_type"),
        sa.CheckConstraint(_in("status", _ITEM_STATUSES), name="ck_configuration_item_status"),
    )
    op.create_index("ix_configuration_items_set", "configuration_items", ["set_id"])

    op.create_table(
        "configuration_versions",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("configuration_item_id", sa.Integer,
                  sa.ForeignKey("configuration_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("value", sa.JSON),
        sa.Column("note", sa.Text),
        sa.Column("changed_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("configuration_item_id", "version", name="uq_configuration_version"),
    )
    op.create_table(
        "configuration_environment_overrides",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("configuration_item_id", sa.Integer,
                  sa.ForeignKey("configuration_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment", sa.Text, nullable=False, server_default="production"),
        sa.Column("value", sa.JSON),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("note", sa.Text),
        sa.Column("override_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("environment", _ENVIRONMENTS), name="ck_configuration_override_environment"),
        sa.UniqueConstraint("configuration_item_id", "environment", name="uq_configuration_override"),
    )
    op.create_table(
        "configuration_preferences",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("scope", sa.Text, nullable=False, server_default="tenant"),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organization_profiles.id", ondelete="SET NULL")),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("preference_key", sa.Text, nullable=False),
        sa.Column("value", sa.JSON),
        sa.Column("reference", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("preference_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("scope", _PREFERENCE_SCOPES), name="ck_configuration_preference_scope"),
        sa.UniqueConstraint("scope", "organization_id", "user_id", "preference_key",
                            name="uq_configuration_preference"),
    )
    op.create_table(
        "configuration_feature_groups",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("group_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "configuration_feature_flags",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("feature_group_id", sa.Integer,
                  sa.ForeignKey("configuration_feature_groups.id", ondelete="SET NULL")),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("rollout_percentage", sa.Integer, nullable=False, server_default="0"),
        sa.Column("target_roles", sa.JSON),
        sa.Column("target_organizations", sa.JSON),
        sa.Column("activation_starts_at", sa.DateTime(timezone=True)),
        sa.Column("activation_ends_at", sa.DateTime(timezone=True)),
        sa.Column("deprecation_at", sa.DateTime(timezone=True)),
        sa.Column("replacement_feature_id", sa.Integer),
        sa.Column("runtime_setting_reference", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("flag_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _FEATURE_STATUSES), name="ck_configuration_feature_status"),
        sa.CheckConstraint("rollout_percentage >= 0 AND rollout_percentage <= 100",
                           name="ck_configuration_feature_rollout_pct"),
    )
    op.create_table(
        "configuration_feature_rollouts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("feature_flag_id", sa.Integer,
                  sa.ForeignKey("configuration_feature_flags.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage", sa.Text, nullable=False),
        sa.Column("percentage", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.Text, nullable=False, server_default="planned"),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("note", sa.Text),
        sa.Column("rollout_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _ROLLOUT_STATUSES), name="ck_configuration_rollout_status"),
        sa.CheckConstraint("percentage >= 0 AND percentage <= 100", name="ck_configuration_rollout_pct"),
    )
    op.create_index("ix_configuration_rollouts_flag", "configuration_feature_rollouts", ["feature_flag_id"])

    op.create_table(
        "configuration_editions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("tier", sa.Text, nullable=False, server_default="standard"),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("description", sa.Text),
        sa.Column("edition_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("tier", _EDITION_TIERS), name="ck_configuration_edition_tier"),
        sa.CheckConstraint(_in("status", _EDITION_STATUSES), name="ck_configuration_edition_status"),
    )
    op.create_table(
        "configuration_edition_capabilities",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("edition_id", sa.Integer,
                  sa.ForeignKey("configuration_editions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("capability_code", sa.Text, nullable=False),
        sa.Column("included", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("edition_id", "capability_code", name="uq_configuration_edition_capability"),
    )
    op.create_table(
        "configuration_license_policies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("edition_id", sa.Integer, sa.ForeignKey("configuration_editions.id", ondelete="SET NULL")),
        sa.Column("max_users", sa.Integer),
        sa.Column("max_organizations", sa.Integer),
        sa.Column("features", sa.JSON),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("effective_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("policy_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _LICENSE_STATUSES), name="ck_configuration_license_status"),
    )
    op.create_table(
        "configuration_edition_assignments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("edition_id", sa.Integer, sa.ForeignKey("configuration_editions.id", ondelete="SET NULL")),
        sa.Column("license_policy_id", sa.Integer,
                  sa.ForeignKey("configuration_license_policies.id", ondelete="SET NULL")),
        sa.Column("scope", sa.Text, nullable=False, server_default="tenant"),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organization_profiles.id", ondelete="SET NULL")),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("assignment_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("scope", _PREFERENCE_SCOPES), name="ck_configuration_assignment_scope"),
        sa.CheckConstraint(_in("status", _ASSIGNMENT_STATUSES), name="ck_configuration_assignment_status"),
    )
    op.create_table(
        "configuration_platform_options",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("option_type", sa.Text, nullable=False, server_default="boolean"),
        sa.Column("value", sa.JSON),
        sa.Column("category", sa.Text),
        sa.Column("editable", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("description", sa.Text),
        sa.Column("option_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("option_type", _OPTION_TYPES), name="ck_configuration_option_type"),
    )
    op.create_table(
        "configuration_administrative_policies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("policy_type", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("config", sa.JSON),
        sa.Column("approved_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("description", sa.Text),
        sa.Column("policy_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _ADMIN_POLICY_STATUSES), name="ck_configuration_admin_policy_status"),
    )
    op.create_table(
        "configuration_runtime_setting_references",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("env_var", sa.Text),
        sa.Column("loader_reference", sa.Text),
        sa.Column("value_type", sa.Text, nullable=False, server_default="string"),
        sa.Column("current_value_note", sa.Text),
        sa.Column("sensitive", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("description", sa.Text),
        sa.Column("reference_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("value_type", _VALUE_TYPES), name="ck_configuration_runtime_value_type"),
    )
    op.create_table(
        "configuration_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("scope", sa.Text),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("payload", sa.JSON),
        sa.Column("summary", sa.Text),
        sa.Column("snapshot_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.create_table(
        "configuration_changes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Integer),
        sa.Column("change_type", sa.Text, nullable=False, server_default="update"),
        sa.Column("from_value", sa.JSON),
        sa.Column("to_value", sa.JSON),
        sa.Column("status", sa.Text, nullable=False, server_default="proposed"),
        sa.Column("requested_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("approved_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("note", sa.Text),
        sa.Column("change_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("change_type", _CHANGE_TYPES), name="ck_configuration_change_type"),
        sa.CheckConstraint(_in("status", _CHANGE_STATUSES), name="ck_configuration_change_status"),
    )
    op.create_index("ix_configuration_changes_status", "configuration_changes", ["status"])

    # Append-only audit ledger (polymorphic; no FK so parent deletes never touch immutable rows).
    op.create_table(
        "configuration_events",
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
    op.create_index("ix_configuration_events_entity", "configuration_events", ["entity_type", "entity_id"])
    op.execute(
        "CREATE OR REPLACE FUNCTION prevent_configuration_event_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'configuration_events are append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER configuration_events_immutable BEFORE UPDATE OR DELETE ON configuration_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_configuration_event_mutation()"
    )

    # Widen the Automation JOB_TYPES CHECKs so Automation may run configuration reviews (D.22 reuse).
    _set_job_type_check("automation_jobs", "ck_automation_job_type", _JOB_TYPES_NEW)
    _set_job_type_check("automation_job_templates", "ck_automation_template_job_type", _JOB_TYPES_NEW)

    # Seed the configuration-category registry (idempotent).
    for code, name, order in _CATEGORY_SEED:
        if bind.execute(sa.text("SELECT id FROM configuration_categories WHERE code=:c"),
                        {"c": code}).scalar() is None:
            bind.execute(sa.text(
                "INSERT INTO configuration_categories (code, name, sort_order) VALUES (:c, :n, :o)"),
                {"c": code, "n": name, "o": order})

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

    op.execute("DROP TRIGGER IF EXISTS configuration_events_immutable ON configuration_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_configuration_event_mutation()")
    op.drop_table("configuration_events")
    op.drop_table("configuration_changes")
    op.drop_table("configuration_snapshots")
    op.drop_table("configuration_runtime_setting_references")
    op.drop_table("configuration_administrative_policies")
    op.drop_table("configuration_platform_options")
    op.drop_table("configuration_edition_assignments")
    op.drop_table("configuration_license_policies")
    op.drop_table("configuration_edition_capabilities")
    op.drop_table("configuration_editions")
    op.drop_table("configuration_feature_rollouts")
    op.drop_table("configuration_feature_flags")
    op.drop_table("configuration_feature_groups")
    op.drop_table("configuration_preferences")
    op.drop_table("configuration_environment_overrides")
    op.drop_table("configuration_versions")
    op.drop_table("configuration_items")
    op.drop_table("configuration_sets")
    op.drop_table("configuration_categories")
