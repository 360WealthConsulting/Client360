"""Declared schema for the Phase D.20 Enterprise Operations platform.

Mirrors the live schema created by migration ``r8c9d0e1f2a3``. Operations is the authoritative
domain for **firm operational metadata only** — projects, phases, milestones, operational tasks,
task dependencies, checklists, operational resources, capacity plans, issues/risks, comments, and
an append-only audit ledger. It **owns no business entities** and is **never a source of truth for
business records**. Every client/business link (person/household/organization, opportunity,
compliance review, communications conversation, workflow instance, advisor-work item, meeting,
document) is an **optional** reference (``ON DELETE SET NULL``) — firm work has no client anchor.

Advisor Work remains the authoritative client-work domain; the ``tasks`` table remains the
authoritative client-task store. Operations models firm operations only and references those
domains without owning them.

``operations_events`` is the append-only audit ledger (trigger-blocked BEFORE UPDATE OR DELETE,
created in the migration). It is polymorphic (``entity_type``/``entity_id``, no FK) so a parent
delete never attempts to mutate an immutable row.
"""
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
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
PROJECT_CATEGORIES = ("tax_season", "audit", "infrastructure", "release", "marketing", "hiring",
                      "onboarding", "policy", "compliance", "operations", "general")
OPERATIONAL_STATUSES = ("planned", "active", "blocked", "on_hold", "completed", "cancelled",
                        "archived")
PRIORITIES = ("low", "normal", "high", "urgent")
HEALTH = ("green", "yellow", "red")
RESOURCE_TYPES = ("staff", "team", "contractor", "equipment", "other")
MILESTONE_STATUSES = ("pending", "reached", "missed")
ISSUE_TYPES = ("risk", "issue")
SEVERITIES = ("low", "medium", "high", "critical")
ISSUE_STATUSES = ("open", "mitigating", "resolved", "accepted", "closed")
DEPENDENCY_TYPES = ("finish_to_start", "start_to_start", "finish_to_finish", "start_to_finish")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def define_operations_tables(metadata: MetaData):
    templates = Table(
        "project_templates", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("category", Text, nullable=False, server_default="general"),
        Column("description", Text),
        Column("default_phases", JSON),         # list of {name, sequence}
        Column("default_tasks", JSON),          # list of {title, phase}
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("tags", JSON),
        Column("template_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("category", PROJECT_CATEGORIES), name="ck_project_template_category"),
    )
    resources = Table(
        "operational_resources", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("resource_type", Text, nullable=False, server_default="staff"),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("department", Text),
        Column("role_title", Text),
        Column("capacity_minutes_per_day", Integer, nullable=False, server_default="480"),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("resource_metadata", JSON),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("resource_type", RESOURCE_TYPES), name="ck_operational_resource_type"),
    )
    projects = Table(
        "projects", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text),
        Column("name", Text, nullable=False),
        Column("category", Text, nullable=False, server_default="general"),
        Column("status", Text, nullable=False, server_default="planned"),
        Column("priority", Text, nullable=False, server_default="normal"),
        Column("health", Text, nullable=False, server_default="green"),
        Column("template_id", Integer, ForeignKey("project_templates.id", ondelete="SET NULL")),
        Column("owner_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("department", Text),
        Column("description", Text),
        Column("start_date", Date),
        Column("target_end_date", Date),
        Column("actual_end_date", Date),
        Column("estimated_minutes", Integer),
        Column("actual_minutes", Integer),
        # Optional client/business references (never owned; firm work has no anchor).
        Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("household_id", Integer, ForeignKey("households.id", ondelete="SET NULL")),
        Column("organization_id", Integer,
               ForeignKey("relationship_entities.id", ondelete="SET NULL")),
        Column("opportunity_id", Integer, ForeignKey("opportunities.id", ondelete="SET NULL")),
        Column("compliance_review_id", BigInteger,
               ForeignKey("compliance_reviews.id", ondelete="SET NULL")),
        Column("conversation_id", Integer,
               ForeignKey("communication_conversations.id", ondelete="SET NULL")),
        Column("workflow_instance_id", Integer,
               ForeignKey("workflow_instances.id", ondelete="SET NULL")),
        Column("tags", JSON),
        Column("project_metadata", JSON),
        Column("last_status_at", DateTime(timezone=True)),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("category", PROJECT_CATEGORIES), name="ck_project_category"),
        CheckConstraint(_in("status", OPERATIONAL_STATUSES), name="ck_project_status"),
        CheckConstraint(_in("priority", PRIORITIES), name="ck_project_priority"),
        CheckConstraint(_in("health", HEALTH), name="ck_project_health"),
    )
    phases = Table(
        "project_phases", metadata,
        Column("id", Integer, primary_key=True),
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        Column("name", Text, nullable=False),
        Column("sequence", Integer, nullable=False, server_default="0"),
        Column("status", Text, nullable=False, server_default="planned"),
        Column("start_date", Date),
        Column("target_end_date", Date),
        Column("description", Text),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", OPERATIONAL_STATUSES), name="ck_project_phase_status"),
    )
    milestones = Table(
        "project_milestones", metadata,
        Column("id", Integer, primary_key=True),
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        Column("phase_id", Integer, ForeignKey("project_phases.id", ondelete="SET NULL")),
        Column("name", Text, nullable=False),
        Column("due_date", Date),
        Column("reached_at", DateTime(timezone=True)),
        Column("status", Text, nullable=False, server_default="pending"),
        Column("description", Text),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", MILESTONE_STATUSES), name="ck_project_milestone_status"),
    )
    tasks = Table(
        "operational_tasks", metadata,
        Column("id", Integer, primary_key=True),
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="SET NULL")),
        Column("phase_id", Integer, ForeignKey("project_phases.id", ondelete="SET NULL")),
        Column("milestone_id", Integer, ForeignKey("project_milestones.id", ondelete="SET NULL")),
        Column("title", Text, nullable=False),
        Column("description", Text),
        Column("status", Text, nullable=False, server_default="planned"),
        Column("priority", Text, nullable=False, server_default="normal"),
        Column("department", Text),
        Column("assigned_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("assigned_resource_id", Integer,
               ForeignKey("operational_resources.id", ondelete="SET NULL")),
        Column("estimated_minutes", Integer),
        Column("actual_minutes", Integer),
        Column("due_date", Date),
        Column("started_at", DateTime(timezone=True)),
        Column("completed_at", DateTime(timezone=True)),
        # Optional cross-domain references (never owned).
        Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("household_id", Integer, ForeignKey("households.id", ondelete="SET NULL")),
        Column("organization_id", Integer,
               ForeignKey("relationship_entities.id", ondelete="SET NULL")),
        Column("advisor_work_item_id", BigInteger,
               ForeignKey("advisor_work_items.id", ondelete="SET NULL")),
        Column("meeting_id", Integer, ForeignKey("meetings.id", ondelete="SET NULL")),
        Column("conversation_id", Integer,
               ForeignKey("communication_conversations.id", ondelete="SET NULL")),
        Column("document_id", Integer, ForeignKey("documents.id", ondelete="SET NULL")),
        Column("workflow_instance_id", Integer,
               ForeignKey("workflow_instances.id", ondelete="SET NULL")),
        Column("tags", JSON),
        Column("task_metadata", JSON),
        Column("last_status_at", DateTime(timezone=True)),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", OPERATIONAL_STATUSES), name="ck_operational_task_status"),
        CheckConstraint(_in("priority", PRIORITIES), name="ck_operational_task_priority"),
    )
    dependencies = Table(
        "operational_task_dependencies", metadata,
        Column("id", Integer, primary_key=True),
        Column("task_id", Integer, ForeignKey("operational_tasks.id", ondelete="CASCADE"),
               nullable=False),
        Column("depends_on_task_id", Integer,
               ForeignKey("operational_tasks.id", ondelete="CASCADE"), nullable=False),
        Column("dependency_type", Text, nullable=False, server_default="finish_to_start"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("dependency_type", DEPENDENCY_TYPES), name="ck_task_dependency_type"),
        CheckConstraint("task_id <> depends_on_task_id", name="ck_task_dependency_self"),
        UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependency"),
    )
    checklist = Table(
        "operational_checklist_items", metadata,
        Column("id", Integer, primary_key=True),
        Column("task_id", Integer, ForeignKey("operational_tasks.id", ondelete="CASCADE"),
               nullable=False),
        Column("description", Text, nullable=False),
        Column("position", Integer, nullable=False, server_default="0"),
        Column("done", Boolean, nullable=False, server_default="false"),
        Column("done_at", DateTime(timezone=True)),
        Column("done_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    capacity = Table(
        "capacity_plans", metadata,
        Column("id", Integer, primary_key=True),
        Column("resource_id", Integer,
               ForeignKey("operational_resources.id", ondelete="CASCADE"), nullable=False),
        Column("period_start", Date, nullable=False),
        Column("period_end", Date, nullable=False),
        Column("planned_minutes", Integer, nullable=False, server_default="0"),
        Column("actual_minutes", Integer, nullable=False, server_default="0"),
        Column("available_minutes", Integer, nullable=False, server_default="0"),
        Column("department", Text),
        Column("notes", Text),
        Column("capacity_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("resource_id", "period_start", "period_end", name="uq_capacity_plan_period"),
    )
    issues = Table(
        "operational_issues", metadata,
        Column("id", Integer, primary_key=True),
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE")),
        Column("task_id", Integer, ForeignKey("operational_tasks.id", ondelete="SET NULL")),
        Column("issue_type", Text, nullable=False, server_default="issue"),
        Column("title", Text, nullable=False),
        Column("description", Text),
        Column("severity", Text, nullable=False, server_default="medium"),
        Column("status", Text, nullable=False, server_default="open"),
        Column("owner_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("due_date", Date),
        Column("resolved_at", DateTime(timezone=True)),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("issue_type", ISSUE_TYPES), name="ck_operational_issue_type"),
        CheckConstraint(_in("severity", SEVERITIES), name="ck_operational_issue_severity"),
        CheckConstraint(_in("status", ISSUE_STATUSES), name="ck_operational_issue_status"),
    )
    comments = Table(
        "operational_comments", metadata,
        Column("id", Integer, primary_key=True),
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE")),
        Column("task_id", Integer, ForeignKey("operational_tasks.id", ondelete="CASCADE")),
        Column("body", Text, nullable=False),
        Column("author_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint("project_id IS NOT NULL OR task_id IS NOT NULL",
                        name="ck_operational_comment_target"),
    )
    # Append-only audit ledger (polymorphic; no FK so parent deletes never touch immutable rows).
    events = Table(
        "operations_events", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("entity_type", Text, nullable=False),   # project | task | milestone | phase | issue
        Column("entity_id", Integer, nullable=False),
        Column("project_id", Integer),
        Column("event_type", Text, nullable=False),
        Column("from_status", Text),
        Column("to_status", Text),
        Column("actor_user_id", Integer),
        Column("payload", JSON),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    return {
        "project_templates": templates,
        "operational_resources": resources,
        "projects": projects,
        "project_phases": phases,
        "project_milestones": milestones,
        "operational_tasks": tasks,
        "operational_task_dependencies": dependencies,
        "operational_checklist_items": checklist,
        "capacity_plans": capacity,
        "operational_issues": issues,
        "operational_comments": comments,
        "operations_events": events,
    }
