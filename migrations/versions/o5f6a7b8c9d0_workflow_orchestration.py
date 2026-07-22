"""Workflow Automation, Orchestration & Business Process Engine (Phase D.17).

A comprehensive workflow engine already exists (templates, versioned steps, dependency DAG,
conditions, SLA, escalations, manual-approval, pause/resume/cancel/complete, the append-only
``workflow_events`` ledger, the ``automation_triggers`` registry, and ``process_event`` which
launches workflows from that registry). D.17 is a deterministic ORCHESTRATION LAYER over that
engine — it reuses ``launch_workflow`` / ``transition_workflow`` / ``complete_step`` /
``process_event`` and adds the genuinely-missing pieces:

- per-step RETRY policy (``retry_count`` / ``max_retries``) and direct step ASSIGNMENT
  (``assigned_user_id``) on ``workflow_steps`` (additive columns; the engine did not have these).
- domain-event TRIGGERS: seeds (INACTIVE, as configuration examples) into the EXISTING
  ``automation_triggers`` table, mapping D.13–D.16 business events to existing published
  templates. Nothing auto-launches until an admin activates a trigger.
- the ``workflow.*`` capability family for the new orchestration surface (the legacy
  ``work.*`` capabilities + ``/workflows`` routes + the tax launcher are untouched).

No new tables; the published templates remain immutable and the existing engine is not modified.
Additive and reversible; capabilities seeded idempotently.
"""
import sqlalchemy as sa
from alembic import op

revision = "o5f6a7b8c9d0"
down_revision = "n4e5f6a7b8c9"
branch_labels = None
depends_on = None

# Deterministic domain-event trigger examples (seeded INACTIVE). (event_type, template_code).
_TRIGGER_SEED = (
    ("opportunity_won", "client_onboarding"),
    ("annual_review_created", "annual_review"),
    ("document_approved", "compliance_review"),
    ("compliance_review_created", "compliance_review"),
    ("campaign_activated", "prospecting"),
    ("referral_added", "prospecting"),
)

_CAPS = (
    ("workflow.view", "View workflow instances, templates, and triggers.", False,
     ("administrator", "advisor", "operations", "compliance")),
    ("workflow.edit", "Edit workflow instances and step assignment.", False,
     ("administrator", "operations")),
    ("workflow.execute", "Launch and advance workflows (steps, retry, approve).", False,
     ("administrator", "advisor", "operations")),
    ("workflow.cancel", "Cancel workflows.", False, ("administrator", "operations")),
    ("workflow.template_manage", "Manage workflow templates and triggers.", False,
     ("administrator",)),
    ("workflow.admin", "Administer the workflow platform.", True, ("administrator",)),
    ("workflow.audit", "View workflow execution history and evidence.", True,
     ("administrator", "compliance")),
)


def upgrade():
    bind = op.get_bind()

    op.add_column("workflow_steps", sa.Column("retry_count", sa.Integer, nullable=False,
                  server_default="0"))
    op.add_column("workflow_steps", sa.Column("max_retries", sa.Integer, nullable=False,
                  server_default="0"))
    op.add_column("workflow_steps", sa.Column("assigned_user_id", sa.Integer,
                  sa.ForeignKey("users.id", ondelete="SET NULL")))

    # Seed example domain-event triggers into the EXISTING automation_triggers table, INACTIVE.
    for event_type, template_code in _TRIGGER_SEED:
        name = f"d17:{event_type}->{template_code}"
        exists = bind.execute(sa.text(
            "SELECT id FROM automation_triggers WHERE name = :n AND event_type = :e"),
            {"n": name, "e": event_type}).scalar()
        if exists is None:
            bind.execute(sa.text(
                "INSERT INTO automation_triggers (name, event_type, entity_type, conditions, "
                "template_code, priority, active) VALUES (:n, :e, :ent, '{}'::json, :t, 100, false)"),
                {"n": name, "e": event_type, "ent": event_type.split("_")[0], "t": template_code})

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
    for event_type, template_code in _TRIGGER_SEED:
        bind.execute(sa.text("DELETE FROM automation_triggers WHERE name = :n"),
                     {"n": f"d17:{event_type}->{template_code}"})
    op.drop_column("workflow_steps", "assigned_user_id")
    op.drop_column("workflow_steps", "max_retries")
    op.drop_column("workflow_steps", "retry_count")
    for code, _d, _s, _roles in _CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is not None:
            bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id = :c"), {"c": cid})
            bind.execute(sa.text("DELETE FROM capabilities WHERE id = :c"), {"c": cid})
