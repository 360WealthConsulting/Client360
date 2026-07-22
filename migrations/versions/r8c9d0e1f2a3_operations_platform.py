"""Enterprise Operations platform (Phase D.20).

Operations is a new AUTHORITATIVE domain for firm operational metadata — projects, phases,
milestones, operational tasks, task dependencies, checklists, operational resources, capacity
plans, issues/risks, comments, and an append-only audit ledger. It **owns no business entities**
and is **never a source of truth for business records**. Every client/business link is an
**optional** reference (``ON DELETE SET NULL``) — firm work has no client anchor. Advisor Work
remains the authoritative client-work domain; the ``tasks`` table remains the authoritative
client-task store. Operations models firm operations only and references those domains.

Tables (12): ``project_templates``, ``operational_resources``, ``projects``, ``project_phases``,
``project_milestones``, ``operational_tasks``, ``operational_task_dependencies``,
``operational_checklist_items``, ``capacity_plans``, ``operational_issues``,
``operational_comments``, and ``operations_events`` (APPEND-ONLY, trigger-blocked).

Seeds 5 ``operations.*`` capabilities and 10 starter project templates. Additive and reversible.
Single Alembic head (down_revision ``q7b8c9d0e1f2`` — the D.19 head).
"""
import sqlalchemy as sa
from alembic import op

revision = "r8c9d0e1f2a3"
down_revision = "q7b8c9d0e1f2"
branch_labels = None
depends_on = None

_CATEGORIES = ("tax_season", "audit", "infrastructure", "release", "marketing", "hiring",
               "onboarding", "policy", "compliance", "operations", "general")
_STATUSES = ("planned", "active", "blocked", "on_hold", "completed", "cancelled", "archived")
_PRIORITIES = ("low", "normal", "high", "urgent")
_HEALTH = ("green", "yellow", "red")
_RESOURCE_TYPES = ("staff", "team", "contractor", "equipment", "other")
_MILESTONE_STATUSES = ("pending", "reached", "missed")
_ISSUE_TYPES = ("risk", "issue")
_SEVERITIES = ("low", "medium", "high", "critical")
_ISSUE_STATUSES = ("open", "mitigating", "resolved", "accepted", "closed")
_DEPENDENCY_TYPES = ("finish_to_start", "start_to_start", "finish_to_finish", "start_to_finish")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


# (code, name, category)
_TEMPLATE_SEED = (
    ("tax_season", "Tax Season", "tax_season"),
    ("ria_audit", "RIA Audit", "audit"),
    ("office_expansion", "Office Expansion", "infrastructure"),
    ("server_migration", "Server Migration", "infrastructure"),
    ("client360_release", "Client360 Release", "release"),
    ("marketing_initiative", "Marketing Initiative", "marketing"),
    ("hiring", "Hiring", "hiring"),
    ("employee_onboarding", "Employee Onboarding", "onboarding"),
    ("policy_rollout", "Policy Rollout", "policy"),
    ("compliance_initiative", "Compliance Initiative", "compliance"),
)

_CAPS = (
    ("operations.view", "View projects, operational tasks, capacity, and resources.", False,
     ("administrator", "operations", "advisor", "compliance")),
    ("operations.manage", "Create, update, and transition projects and operational tasks.", False,
     ("administrator", "operations")),
    ("operations.templates", "Manage project templates and operational resources.", False,
     ("administrator", "operations")),
    ("operations.audit", "View operations audit history.", True, ("administrator", "compliance")),
    ("operations.admin", "Administer the operations platform.", True, ("administrator",)),
)


def upgrade():
    bind = op.get_bind()

    op.create_table(
        "project_templates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False, server_default="general"),
        sa.Column("description", sa.Text),
        sa.Column("default_phases", sa.JSON),
        sa.Column("default_tasks", sa.JSON),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("tags", sa.JSON),
        sa.Column("template_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("category", _CATEGORIES), name="ck_project_template_category"),
    )

    op.create_table(
        "operational_resources",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("resource_type", sa.Text, nullable=False, server_default="staff"),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("department", sa.Text),
        sa.Column("role_title", sa.Text),
        sa.Column("capacity_minutes_per_day", sa.Integer, nullable=False, server_default="480"),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("resource_metadata", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("resource_type", _RESOURCE_TYPES), name="ck_operational_resource_type"),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False, server_default="general"),
        sa.Column("status", sa.Text, nullable=False, server_default="planned"),
        sa.Column("priority", sa.Text, nullable=False, server_default="normal"),
        sa.Column("health", sa.Text, nullable=False, server_default="green"),
        sa.Column("template_id", sa.Integer, sa.ForeignKey("project_templates.id", ondelete="SET NULL")),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("department", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("start_date", sa.Date),
        sa.Column("target_end_date", sa.Date),
        sa.Column("actual_end_date", sa.Date),
        sa.Column("estimated_minutes", sa.Integer),
        sa.Column("actual_minutes", sa.Integer),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="SET NULL")),
        sa.Column("opportunity_id", sa.Integer, sa.ForeignKey("opportunities.id", ondelete="SET NULL")),
        sa.Column("compliance_review_id", sa.BigInteger,
                  sa.ForeignKey("compliance_reviews.id", ondelete="SET NULL")),
        sa.Column("conversation_id", sa.Integer,
                  sa.ForeignKey("communication_conversations.id", ondelete="SET NULL")),
        sa.Column("workflow_instance_id", sa.Integer,
                  sa.ForeignKey("workflow_instances.id", ondelete="SET NULL")),
        sa.Column("tags", sa.JSON),
        sa.Column("project_metadata", sa.JSON),
        sa.Column("last_status_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("category", _CATEGORIES), name="ck_project_category"),
        sa.CheckConstraint(_in("status", _STATUSES), name="ck_project_status"),
        sa.CheckConstraint(_in("priority", _PRIORITIES), name="ck_project_priority"),
        sa.CheckConstraint(_in("health", _HEALTH), name="ck_project_health"),
    )
    op.create_index("ix_projects_status", "projects", ["status"])
    op.create_index("ix_projects_owner", "projects", ["owner_user_id"])

    op.create_table(
        "project_phases",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.Text, nullable=False, server_default="planned"),
        sa.Column("start_date", sa.Date),
        sa.Column("target_end_date", sa.Date),
        sa.Column("description", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _STATUSES), name="ck_project_phase_status"),
    )
    op.create_index("ix_project_phases_project", "project_phases", ["project_id"])

    op.create_table(
        "project_milestones",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("phase_id", sa.Integer, sa.ForeignKey("project_phases.id", ondelete="SET NULL")),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("due_date", sa.Date),
        sa.Column("reached_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("description", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _MILESTONE_STATUSES), name="ck_project_milestone_status"),
    )
    op.create_index("ix_project_milestones_project", "project_milestones", ["project_id"])

    op.create_table(
        "operational_tasks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="SET NULL")),
        sa.Column("phase_id", sa.Integer, sa.ForeignKey("project_phases.id", ondelete="SET NULL")),
        sa.Column("milestone_id", sa.Integer, sa.ForeignKey("project_milestones.id", ondelete="SET NULL")),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="planned"),
        sa.Column("priority", sa.Text, nullable=False, server_default="normal"),
        sa.Column("department", sa.Text),
        sa.Column("assigned_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("assigned_resource_id", sa.Integer,
                  sa.ForeignKey("operational_resources.id", ondelete="SET NULL")),
        sa.Column("estimated_minutes", sa.Integer),
        sa.Column("actual_minutes", sa.Integer),
        sa.Column("due_date", sa.Date),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="SET NULL")),
        sa.Column("advisor_work_item_id", sa.BigInteger,
                  sa.ForeignKey("advisor_work_items.id", ondelete="SET NULL")),
        sa.Column("meeting_id", sa.Integer, sa.ForeignKey("meetings.id", ondelete="SET NULL")),
        sa.Column("conversation_id", sa.Integer,
                  sa.ForeignKey("communication_conversations.id", ondelete="SET NULL")),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("workflow_instance_id", sa.Integer,
                  sa.ForeignKey("workflow_instances.id", ondelete="SET NULL")),
        sa.Column("tags", sa.JSON),
        sa.Column("task_metadata", sa.JSON),
        sa.Column("last_status_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _STATUSES), name="ck_operational_task_status"),
        sa.CheckConstraint(_in("priority", _PRIORITIES), name="ck_operational_task_priority"),
    )
    op.create_index("ix_operational_tasks_project", "operational_tasks", ["project_id"])
    op.create_index("ix_operational_tasks_status", "operational_tasks", ["status"])
    op.create_index("ix_operational_tasks_assignee", "operational_tasks", ["assigned_user_id"])

    op.create_table(
        "operational_task_dependencies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("operational_tasks.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("depends_on_task_id", sa.Integer,
                  sa.ForeignKey("operational_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dependency_type", sa.Text, nullable=False, server_default="finish_to_start"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("dependency_type", _DEPENDENCY_TYPES), name="ck_task_dependency_type"),
        sa.CheckConstraint("task_id <> depends_on_task_id", name="ck_task_dependency_self"),
        sa.UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependency"),
    )
    op.create_index("ix_task_dependencies_task", "operational_task_dependencies", ["task_id"])

    op.create_table(
        "operational_checklist_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("operational_tasks.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("done", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("done_at", sa.DateTime(timezone=True)),
        sa.Column("done_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_checklist_items_task", "operational_checklist_items", ["task_id"])

    op.create_table(
        "capacity_plans",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("resource_id", sa.Integer,
                  sa.ForeignKey("operational_resources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column("planned_minutes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("actual_minutes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("available_minutes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("department", sa.Text),
        sa.Column("notes", sa.Text),
        sa.Column("capacity_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("resource_id", "period_start", "period_end", name="uq_capacity_plan_period"),
    )
    op.create_index("ix_capacity_plans_resource", "capacity_plans", ["resource_id"])

    op.create_table(
        "operational_issues",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("operational_tasks.id", ondelete="SET NULL")),
        sa.Column("issue_type", sa.Text, nullable=False, server_default="issue"),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("severity", sa.Text, nullable=False, server_default="medium"),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("due_date", sa.Date),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("issue_type", _ISSUE_TYPES), name="ck_operational_issue_type"),
        sa.CheckConstraint(_in("severity", _SEVERITIES), name="ck_operational_issue_severity"),
        sa.CheckConstraint(_in("status", _ISSUE_STATUSES), name="ck_operational_issue_status"),
    )
    op.create_index("ix_operational_issues_project", "operational_issues", ["project_id"])

    op.create_table(
        "operational_comments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("operational_tasks.id", ondelete="CASCADE")),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("author_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("project_id IS NOT NULL OR task_id IS NOT NULL",
                           name="ck_operational_comment_target"),
    )
    op.create_index("ix_operational_comments_project", "operational_comments", ["project_id"])
    op.create_index("ix_operational_comments_task", "operational_comments", ["task_id"])

    # Append-only audit ledger (polymorphic; no FK so parent deletes never touch immutable rows).
    op.create_table(
        "operations_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("project_id", sa.Integer),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("from_status", sa.Text),
        sa.Column("to_status", sa.Text),
        sa.Column("actor_user_id", sa.Integer),
        sa.Column("payload", sa.JSON),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_operations_events_entity", "operations_events", ["entity_type", "entity_id"])
    op.create_index("ix_operations_events_project", "operations_events", ["project_id"])
    op.execute(
        "CREATE OR REPLACE FUNCTION prevent_operations_event_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'operations_events are append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER operations_events_immutable BEFORE UPDATE OR DELETE ON operations_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_operations_event_mutation()"
    )

    # Seed reusable starter project templates (idempotent by code).
    for code, name, category in _TEMPLATE_SEED:
        exists = bind.execute(sa.text("SELECT id FROM project_templates WHERE code = :c"),
                              {"c": code}).scalar()
        if exists is None:
            bind.execute(sa.text(
                "INSERT INTO project_templates (code, name, category, active) "
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

    op.execute("DROP TRIGGER IF EXISTS operations_events_immutable ON operations_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_operations_event_mutation()")
    op.drop_table("operations_events")
    op.drop_table("operational_comments")
    op.drop_table("operational_issues")
    op.drop_table("capacity_plans")
    op.drop_table("operational_checklist_items")
    op.drop_table("operational_task_dependencies")
    op.drop_table("operational_tasks")
    op.drop_table("project_milestones")
    op.drop_table("project_phases")
    op.drop_table("projects")
    op.drop_table("operational_resources")
    op.drop_table("project_templates")
