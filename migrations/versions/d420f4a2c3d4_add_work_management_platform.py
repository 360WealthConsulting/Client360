"""add operational work management platform

Revision ID: d420f4a2c3d4
Revises: c410f4a1b2c3
"""
from alembic import op
import sqlalchemy as sa
from app.database.schema import metadata

revision = "d420f4a2c3d4"
down_revision = "c410f4a1b2c3"
branch_labels = None
depends_on = None

WORK_TABLES = (
    "workflow_instances", "workflow_steps", "assignment_rules",
    "work_assignment_details", "assignment_events", "work_queues", "work_approvals",
)
QUEUES = (
    ("waiting_on_client", "Waiting on Client", {"waiting_on": "client"}),
    ("waiting_on_staff", "Waiting on Staff", {"waiting_on": "staff"}),
    ("waiting_on_cpa", "Waiting on CPA", {"waiting_on": "cpa"}),
    ("waiting_on_custodian", "Waiting on Custodian", {"waiting_on": "custodian"}),
    ("waiting_on_attorney", "Waiting on Attorney", {"waiting_on": "attorney"}),
    ("ready_to_review", "Ready to Review", {"status": "ready_for_review"}),
    ("ready_for_delivery", "Ready for Delivery", {"status": "ready_for_delivery"}),
    ("high_priority", "High Priority", {"minimum_priority": "high"}),
    ("compliance", "Compliance", {"work_type": "compliance"}),
    ("overdue", "Overdue", {"overdue": True}),
    ("blocked", "Blocked", {"status": "blocked"}),
    ("unassigned", "Unassigned", {"unassigned": True}),
)


def upgrade():
    bind = op.get_bind()
    op.alter_column("record_assignments", "user_id", existing_type=sa.Integer(), nullable=True)
    op.create_check_constraint(
        "ck_record_assignment_owner", "record_assignments",
        "user_id IS NOT NULL OR team_id IS NOT NULL",
    )
    op.create_index(
        "uq_record_assignment_active_primary", "record_assignments",
        ["entity_type", "entity_id"], unique=True,
        postgresql_where=sa.text("assignment_type = 'primary' AND inactive_date IS NULL"),
    )
    for name in WORK_TABLES:
        metadata.tables[name].create(bind)

    for name, column in (
        ("household_id", sa.Column("household_id", sa.Integer())),
        ("team_id", sa.Column("team_id", sa.Integer())),
        ("workflow_name", sa.Column("workflow_name", sa.String(255))),
        ("work_type", sa.Column("work_type", sa.String(100), nullable=False, server_default="general")),
        ("waiting_on", sa.Column("waiting_on", sa.String(50))),
        ("sla_due_at", sa.Column("sla_due_at", sa.DateTime(timezone=True))),
        ("estimated_minutes", sa.Column("estimated_minutes", sa.Integer(), nullable=False, server_default="30")),
    ):
        op.add_column("tasks", column)
    op.create_foreign_key("fk_tasks_household_work", "tasks", "households", ["household_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("fk_tasks_team_work", "tasks", "teams", ["team_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_tasks_work_queue", "tasks", ["status", "due_date", "priority"])
    op.create_index("ix_tasks_sla_due_at", "tasks", ["sla_due_at"])

    op.add_column("documents", sa.Column("review_status", sa.String(50), nullable=False, server_default="not_required"))
    op.add_column("documents", sa.Column("review_due_at", sa.DateTime(timezone=True)))
    op.add_column("documents", sa.Column("reviewer_team_id", sa.Integer()))
    op.create_foreign_key("fk_documents_reviewer_team", "documents", "teams", ["reviewer_team_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_documents_review_queue", "documents", ["review_status", "review_due_at"])

    capabilities = metadata.tables["capabilities"]
    role_capabilities = metadata.tables["role_capabilities"]
    roles = metadata.tables["roles"]
    capability_rows = (
        ("work.read", "View authorized operational dashboards, work, and queues.", False),
        ("work.write", "Create and update authorized operational work.", False),
        ("capacity.read", "View team workload and capacity metrics.", True),
    )
    for table_name in ("capabilities", "roles", "teams"):
        op.execute(sa.text(
            f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table_name}), 1), true)"
        ))
    for code, description, sensitive in capability_rows:
        bind.execute(capabilities.insert().values(code=code, description=description, sensitive=sensitive))
    capability_ids = {row.code: row.id for row in bind.execute(sa.select(capabilities.c.code, capabilities.c.id).where(capabilities.c.code.in_([r[0] for r in capability_rows])))}
    role_ids = {row.code: row.id for row in bind.execute(sa.select(roles.c.code, roles.c.id))}
    grants = {
        "administrator": ("work.read", "work.write", "capacity.read"),
        "advisor": ("work.read", "work.write"),
        "operations": ("work.read", "work.write", "capacity.read"),
        "compliance": ("work.read", "capacity.read"),
    }
    bind.execute(role_capabilities.insert(), [
        {"role_id": role_ids[role], "capability_id": capability_ids[code]}
        for role, codes in grants.items() for code in codes
    ])
    bind.execute(metadata.tables["work_queues"].insert(), [
        {"code": code, "name": name, "description": f"Reusable {name.lower()} operational queue.", "criteria": criteria}
        for code, name, criteria in QUEUES
    ])
    op.execute("CREATE FUNCTION prevent_assignment_event_mutation() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'assignment_events are append-only'; END; $$ LANGUAGE plpgsql")
    op.execute("CREATE TRIGGER assignment_events_immutable BEFORE UPDATE OR DELETE ON assignment_events FOR EACH ROW EXECUTE FUNCTION prevent_assignment_event_mutation()")


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS assignment_events_immutable ON assignment_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_assignment_event_mutation()")
    op.drop_index("ix_documents_review_queue", table_name="documents")
    op.drop_constraint("fk_documents_reviewer_team", "documents", type_="foreignkey")
    for column in ("reviewer_team_id", "review_due_at", "review_status"):
        op.drop_column("documents", column)
    op.drop_index("ix_tasks_sla_due_at", table_name="tasks")
    op.drop_index("ix_tasks_work_queue", table_name="tasks")
    op.drop_constraint("fk_tasks_team_work", "tasks", type_="foreignkey")
    op.drop_constraint("fk_tasks_household_work", "tasks", type_="foreignkey")
    for column in ("estimated_minutes", "sla_due_at", "waiting_on", "work_type", "workflow_name", "team_id", "household_id"):
        op.drop_column("tasks", column)
    bind = op.get_bind()
    capability_ids = sa.select(metadata.tables["capabilities"].c.id).where(metadata.tables["capabilities"].c.code.in_(("work.read", "work.write", "capacity.read")))
    bind.execute(metadata.tables["role_capabilities"].delete().where(metadata.tables["role_capabilities"].c.capability_id.in_(capability_ids)))
    bind.execute(metadata.tables["capabilities"].delete().where(metadata.tables["capabilities"].c.code.in_(("work.read", "work.write", "capacity.read"))))
    for name in reversed(WORK_TABLES):
        metadata.tables[name].drop(bind)
    op.drop_index("uq_record_assignment_active_primary", table_name="record_assignments")
    op.drop_constraint("ck_record_assignment_owner", "record_assignments", type_="check")
    op.execute("DELETE FROM record_assignments WHERE user_id IS NULL")
    op.alter_column("record_assignments", "user_id", existing_type=sa.Integer(), nullable=False)
