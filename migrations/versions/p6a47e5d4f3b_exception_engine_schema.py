"""exception engine schema: platform-wide exceptions (tax domain first)

Revision ID: p6a47e5d4f3b
Revises: o5f36c4d3e2a

Release 0.9.10 (Sprint 5.5), Phase 1 — Exception Engine schema (ADR-17). Adds the
platform-wide exception tables (`exception_types`, `exceptions`, `exception_events`) with a
required CHECK-constrained `domain`, an append-only trigger on the event ledger, hot-path
indexes, and the tax-domain reference seed (exception types, `exception.*` capabilities +
role grants, and work queues). Additive and reversible; single head. Only `domain='tax'` is
seeded; the other domains are schema-ready but unimplemented.

Indexes are created normally (not CONCURRENTLY): the tables are brand-new and empty in this
migration, so there is no production lock contention to avoid.
"""
from alembic import op
import sqlalchemy as sa

revision = "p6a47e5d4f3b"
down_revision = "o5f36c4d3e2a"
branch_labels = None
depends_on = None

DOMAINS = ("tax", "wealth", "operations", "compliance", "portal", "microsoft")
CATEGORIES = ("client", "workflow", "document", "filing", "compliance", "operational")
SEVERITIES = ("blocker", "high", "medium", "low")
STATUSES = ("open", "acknowledged", "in_progress", "waiting", "escalated", "resolved",
            "cancelled", "reopened")
SOURCES = ("system", "manual", "portal", "microsoft")
SLA_BY_SEVERITY = {"blocker": 1440, "high": 2880, "medium": 7200, "low": 14400}

# (code, category, severity, owner_role, blocks_lifecycle, compliance_visible, name)
# domain='tax' for all Sprint 5.5 rows; owner roles use real base roles (advisor /
# operations / compliance / administrator) — the demo-only tax_preparer role is granted
# separately in the demo seed.
TAX_EXCEPTION_TYPES = [
    ("CLIENT_UNRESPONSIVE", "client", "medium", "advisor", False, False, "Client unresponsive"),
    ("CLIENT_EFILE_AUTH_MISSING", "client", "high", "advisor", False, False, "E-file authorization missing"),
    ("CLIENT_ENGAGEMENT_UNSIGNED", "client", "medium", "advisor", False, False, "Engagement letter unsigned"),
    ("CLIENT_INFO_INCONSISTENT", "client", "high", "advisor", False, False, "Client information inconsistent"),
    ("WORKFLOW_SLA_BREACH", "workflow", "high", "operations", False, False, "Workflow SLA breach"),
    ("WORKFLOW_STUCK", "workflow", "medium", "operations", False, False, "Workflow stuck"),
    ("WORKFLOW_STEP_FAILED", "workflow", "high", "operations", False, False, "Workflow step failed"),
    ("WORKFLOW_DEADLOCK", "workflow", "high", "operations", False, False, "Workflow dependency deadlock"),
    ("DOC_MISSING_OVERDUE", "document", "medium", "operations", False, False, "Required document overdue"),
    ("DOC_AMBIGUOUS_MATCH", "document", "medium", "operations", False, False, "Ambiguous document match"),
    ("DOC_REVIEW_REJECTED", "document", "medium", "operations", False, False, "Document review rejected"),
    ("DOC_UNREADABLE", "document", "low", "operations", False, False, "Document unreadable/invalid"),
    ("FILING_REJECTED", "filing", "blocker", "operations", True, False, "Filing rejected"),
    ("FILING_DEADLINE_AT_RISK", "filing", "high", "operations", False, False, "Filing deadline at risk"),
    ("FILING_TRANSMISSION_ERROR", "filing", "blocker", "operations", True, False, "Filing transmission error"),
    ("FILING_AMENDMENT_REQUIRED", "filing", "high", "operations", False, False, "Amendment required"),
    ("COMPLIANCE_SIGNOFF_MISSING", "compliance", "blocker", "compliance", True, True, "Required sign-off missing"),
    ("COMPLIANCE_SOD_VIOLATION", "compliance", "blocker", "compliance", True, True, "Segregation-of-duty violation"),
    ("COMPLIANCE_ACCESS_ANOMALY", "compliance", "high", "compliance", False, True, "Access anomaly"),
    ("COMPLIANCE_RETENTION_RISK", "compliance", "medium", "compliance", False, True, "Document retention risk"),
    ("OPS_MICROSOFT_SYNC_FAILED", "operational", "medium", "operations", False, False, "Microsoft sync failed"),
    ("OPS_TOKEN_RECONNECT_REQUIRED", "operational", "high", "operations", False, False, "Microsoft reconnect required"),
    ("OPS_JOB_FAILURE", "operational", "high", "administrator", False, False, "Scheduler job failure"),
    ("OPS_IMPORT_ERROR", "operational", "medium", "operations", False, False, "Import error"),
]


def _check(col, allowed):
    values = ", ".join(f"'{v}'" for v in allowed)
    return f"{col} IN ({values})"


def upgrade():
    op.create_table(
        "exception_types",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("domain", sa.String(30), nullable=False),
        sa.Column("code", sa.String(80), nullable=False, unique=True),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("default_severity", sa.String(20), nullable=False),
        sa.Column("trigger_kind", sa.String(20), nullable=False, server_default="auto"),
        sa.Column("default_owner_role", sa.String(50)),
        sa.Column("default_owner_team", sa.String(50)),
        sa.Column("sla_minutes", sa.Integer, nullable=False, server_default="2880"),
        sa.Column("escalation_policy", sa.JSON, nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("notification_policy", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("resolution_options", sa.JSON, nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("blocks_lifecycle", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("compliance_visible", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(_check("domain", DOMAINS), name="ck_exception_types_domain"),
        sa.CheckConstraint(_check("category", CATEGORIES), name="ck_exception_types_category"),
        sa.CheckConstraint(_check("default_severity", SEVERITIES), name="ck_exception_types_severity"),
        sa.CheckConstraint(_check("trigger_kind", ("auto", "manual")), name="ck_exception_types_trigger"),
    )

    op.create_table(
        "exceptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("exception_type_id", sa.Integer, sa.ForeignKey("exception_types.id"), nullable=False),
        sa.Column("domain", sa.String(30), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("source", sa.String(20), nullable=False, server_default="system"),
        sa.Column("tax_engagement_return_id", sa.Integer, sa.ForeignKey("tax_engagement_returns.id")),
        sa.Column("tax_engagement_id", sa.Integer, sa.ForeignKey("tax_engagements.id")),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id")),
        sa.Column("workflow_instance_id", sa.Integer, sa.ForeignKey("workflow_instances.id")),
        sa.Column("workflow_step_id", sa.Integer, sa.ForeignKey("workflow_steps.id")),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id")),
        sa.Column("related_entity_type", sa.String(50)),
        sa.Column("related_entity_id", sa.Integer),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("owner_team_id", sa.Integer, sa.ForeignKey("teams.id")),
        sa.Column("assignment_id", sa.Integer, sa.ForeignKey("record_assignments.id")),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("sla_due_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("escalation_level", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_notified_at", sa.DateTime(timezone=True)),
        sa.Column("notification_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("resolution_code", sa.String(50)),
        sa.Column("resolution_notes", sa.Text),
        sa.Column("resolved_by_user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("dedupe_key", sa.String(255)),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(_check("domain", DOMAINS), name="ck_exceptions_domain"),
        sa.CheckConstraint(_check("category", CATEGORIES), name="ck_exceptions_category"),
        sa.CheckConstraint(_check("severity", SEVERITIES), name="ck_exceptions_severity"),
        sa.CheckConstraint(_check("status", STATUSES), name="ck_exceptions_status"),
        sa.CheckConstraint(_check("source", SOURCES), name="ck_exceptions_source"),
    )

    op.create_table(
        "exception_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("exception_id", sa.Integer, sa.ForeignKey("exceptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("from_status", sa.String(20)),
        sa.Column("to_status", sa.String(20)),
        sa.Column("actor_user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("portal_account_id", sa.Integer, sa.ForeignKey("portal_accounts.id")),
        sa.Column("metadata", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # Hot-path indexes (empty new tables → plain create, no CONCURRENTLY needed).
    op.create_index("ix_exceptions_domain_status", "exceptions", ["domain", "status"])
    op.create_index("ix_exceptions_return", "exceptions", ["tax_engagement_return_id"])
    op.create_index("ix_exceptions_person", "exceptions", ["person_id"])
    op.create_index("ix_exceptions_household", "exceptions", ["household_id"])
    op.create_index("ix_exceptions_status_severity", "exceptions", ["status", "severity"])
    op.create_index("ix_exceptions_category_status", "exceptions", ["category", "status"])
    op.create_index("ix_exceptions_owner_user", "exceptions", ["owner_user_id"])
    op.create_index("ix_exceptions_owner_team", "exceptions", ["owner_team_id"])
    op.execute(
        "CREATE INDEX ix_exceptions_sla_open ON exceptions (sla_due_at) "
        "WHERE status NOT IN ('resolved','cancelled')"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_exceptions_dedupe_active ON exceptions (dedupe_key) "
        "WHERE dedupe_key IS NOT NULL AND status NOT IN ('resolved','cancelled')"
    )
    op.create_index("ix_exception_events_exception", "exception_events", ["exception_id"])

    # Append-only ledger (same idiom as audit_events / tax_return_lifecycle_events).
    op.execute(
        "CREATE FUNCTION prevent_exception_event_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'exception_events are append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER exception_events_immutable BEFORE UPDATE OR DELETE ON exception_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_exception_event_mutation()"
    )

    bind = op.get_bind()

    # Seed the tax-domain exception types.
    types_table = sa.table(
        "exception_types",
        sa.column("domain"), sa.column("code"), sa.column("category"), sa.column("name"),
        sa.column("default_severity"), sa.column("default_owner_role"), sa.column("sla_minutes"),
        sa.column("blocks_lifecycle"), sa.column("compliance_visible"),
    )
    op.bulk_insert(types_table, [
        {"domain": "tax", "code": code, "category": category, "name": name,
         "default_severity": severity, "default_owner_role": owner,
         "sla_minutes": SLA_BY_SEVERITY[severity], "blocks_lifecycle": blocks,
         "compliance_visible": comp_vis}
        for (code, category, severity, owner, blocks, comp_vis, name) in TAX_EXCEPTION_TYPES
    ])

    # New capability family (least-privilege; resolve/compliance are sensitive).
    bind.execute(sa.text(
        "INSERT INTO capabilities (code, description, sensitive) VALUES "
        "('exception.read','View exceptions',false),"
        "('exception.write','Raise/acknowledge/assign/resolve non-blocker exceptions',false),"
        "('exception.resolve','Resolve blocker exceptions',true),"
        "('exception.compliance','Resolve compliance-category exceptions',true)"
    ))

    def grant(cap, roles):
        role_list = ", ".join(f"'{r}'" for r in roles)
        bind.execute(sa.text(
            "INSERT INTO role_capabilities (role_id, capability_id) "
            "SELECT r.id, c.id FROM roles r CROSS JOIN capabilities c "
            f"WHERE c.code = :cap AND r.code IN ({role_list}) "
            "ON CONFLICT DO NOTHING"
        ), {"cap": cap})

    grant("exception.read", ("administrator", "advisor", "operations", "compliance"))
    grant("exception.write", ("administrator", "advisor", "operations"))
    grant("exception.resolve", ("administrator",))
    grant("exception.compliance", ("administrator", "compliance"))

    # Work queues (config rows consumed by work_intelligence).
    bind.execute(sa.text(
        "INSERT INTO work_queues (code, name, description, criteria, required_capability) VALUES "
        "('exceptions','Exceptions','Open exceptions', CAST('{}' AS json), 'exception.read'),"
        "('exceptions_critical','Critical exceptions','Blocker/high exceptions', CAST('{}' AS json), 'exception.resolve'),"
        "('compliance_exceptions','Compliance exceptions','Compliance-category exceptions', CAST('{}' AS json), 'exception.compliance')"
    ))


def downgrade():
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM work_queues WHERE code IN ('exceptions','exceptions_critical','compliance_exceptions')"))
    bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id IN (SELECT id FROM capabilities WHERE code LIKE 'exception.%')"))
    bind.execute(sa.text("DELETE FROM capabilities WHERE code LIKE 'exception.%'"))
    op.execute("DROP TRIGGER IF EXISTS exception_events_immutable ON exception_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_exception_event_mutation()")
    op.drop_table("exception_events")
    op.drop_table("exceptions")
    op.drop_table("exception_types")
