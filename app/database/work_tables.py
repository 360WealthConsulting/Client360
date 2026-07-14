from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Integer, JSON, MetaData,
    String, Table, Text, UniqueConstraint, func,
)


def define_work_tables(metadata: MetaData):
    workflow_instances = Table(
        "workflow_instances", metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(255), nullable=False),
        Column("workflow_type", String(100), nullable=False, server_default="general"),
        Column("person_id", Integer, ForeignKey("people.id", ondelete="CASCADE")),
        Column("household_id", Integer, ForeignKey("households.id", ondelete="CASCADE")),
        Column("status", String(50), nullable=False, server_default="active"),
        Column("priority", String(50), nullable=False, server_default="normal"),
        Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("due_date", Date), Column("completed_at", DateTime(timezone=True)),
        Column("metadata", JSON, nullable=False, server_default="{}"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    workflow_steps = Table(
        "workflow_steps", metadata,
        Column("id", Integer, primary_key=True),
        Column("workflow_instance_id", Integer, ForeignKey("workflow_instances.id", ondelete="CASCADE"), nullable=False),
        Column("name", String(255), nullable=False), Column("sequence", Integer, nullable=False),
        Column("status", String(50), nullable=False, server_default="pending"),
        Column("priority", String(50), nullable=False, server_default="normal"),
        Column("due_date", Date), Column("sla_due_at", DateTime(timezone=True)),
        Column("estimated_minutes", Integer, nullable=False, server_default="30"),
        Column("waiting_on", String(50)), Column("blocked_reason", Text),
        Column("requires_approval", Boolean, nullable=False, server_default="false"),
        Column("completed_at", DateTime(timezone=True)),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("workflow_instance_id", "sequence", name="uq_workflow_step_sequence"),
    )
    assignment_rules = Table(
        "assignment_rules", metadata,
        Column("id", Integer, primary_key=True), Column("name", String(255), nullable=False),
        Column("entity_type", String(50), nullable=False),
        Column("conditions", JSON, nullable=False, server_default="{}"),
        Column("assignment_role", String(50), nullable=False, server_default="primary"),
        Column("assignee_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("assignee_team_id", Integer, ForeignKey("teams.id", ondelete="SET NULL")),
        Column("priority", Integer, nullable=False, server_default="100"),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("name", "entity_type", name="uq_assignment_rule_name_type"),
    )
    work_assignment_details = Table(
        "work_assignment_details", metadata,
        Column("assignment_id", Integer, ForeignKey("record_assignments.id", ondelete="CASCADE"), primary_key=True),
        Column("assignment_rule_id", Integer, ForeignKey("assignment_rules.id", ondelete="SET NULL")),
        Column("assigned_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("reason", Text), Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("ended_at", DateTime(timezone=True)),
    )
    assignment_events = Table(
        "assignment_events", metadata,
        Column("id", Integer, primary_key=True),
        Column("assignment_id", Integer, ForeignKey("record_assignments.id", ondelete="SET NULL")),
        Column("entity_type", String(50), nullable=False), Column("entity_id", Integer, nullable=False),
        Column("event_type", String(50), nullable=False),
        Column("from_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("to_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("from_team_id", Integer, ForeignKey("teams.id", ondelete="SET NULL")),
        Column("to_team_id", Integer, ForeignKey("teams.id", ondelete="SET NULL")),
        Column("assignment_role", String(50), nullable=False), Column("reason", Text),
        Column("actor_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    work_queues = Table(
        "work_queues", metadata,
        Column("id", Integer, primary_key=True), Column("code", String(100), nullable=False, unique=True),
        Column("name", String(255), nullable=False), Column("description", Text),
        Column("criteria", JSON, nullable=False, server_default="{}"),
        Column("required_capability", String(150), nullable=False, server_default="work.read"),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    work_approvals = Table(
        "work_approvals", metadata,
        Column("id", Integer, primary_key=True), Column("entity_type", String(50), nullable=False),
        Column("entity_id", Integer, nullable=False), Column("approval_type", String(100), nullable=False),
        Column("status", String(50), nullable=False, server_default="pending"),
        Column("requested_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("approver_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("approver_team_id", Integer, ForeignKey("teams.id", ondelete="SET NULL")),
        Column("due_at", DateTime(timezone=True)), Column("decided_at", DateTime(timezone=True)),
        Column("decision_notes", Text), Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("entity_type", "entity_id", "approval_type", name="uq_work_approval"),
    )
    tables = (workflow_instances, workflow_steps, assignment_rules,
              work_assignment_details, assignment_events, work_queues, work_approvals)
    return {table.name: table for table in tables}
