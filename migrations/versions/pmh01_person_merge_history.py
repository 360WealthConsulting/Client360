"""person_merge_history — permanent audit ledger of canonical person merges (MDM-1)

Records every canonical person merge so the survivor's provenance is reconstructable long after the
merged (duplicate) person row is removed. The merged/survivor person ids are stored as PLAIN INTEGERS
(no foreign key) on purpose: the history must remain valid — and the id values must be retained — even
after the duplicate ``people`` row is deleted, so a restrictive FK (which would block the delete) or an
ON DELETE SET NULL / CASCADE (which would lose or delete the history) is deliberately avoided.
``actor_user_id`` keeps an ON DELETE SET NULL FK to users (the actor may leave; the record stays).

Revision ID: pmh01
Revises: docknow01
Create Date: 2026-08-03
"""
import sqlalchemy as sa
from alembic import op

revision = "pmh01"
down_revision = "docknow01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "person_merge_history",
        sa.Column("id", sa.Integer, primary_key=True),
        # No FK: the value must survive deletion of the merged person row (see module docstring).
        sa.Column("survivor_person_id", sa.Integer, nullable=False),
        sa.Column("merged_person_id", sa.Integer, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("merge_method", sa.Text, nullable=False, server_default="manual_review"),
        sa.Column("actor_user_id", sa.Integer,
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("pre_merge_snapshot", sa.dialects.postgresql.JSONB, nullable=False,
                  server_default="{}"),
        sa.Column("merge_summary", sa.dialects.postgresql.JSONB, nullable=False,
                  server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_person_merge_history_survivor", "person_merge_history", ["survivor_person_id"])
    op.create_index("ix_person_merge_history_merged", "person_merge_history", ["merged_person_id"])

    # Register the new domain-event contract + its dark-launched projector subscription (idempotent).
    # A fresh database also gets this from the D.35 seed migration (it reads the current seed list); this
    # block covers databases already migrated past that point. Ids/statuses only — references-only.
    import json as _json
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT id FROM domain_event_contracts WHERE event_type=:e"),
                    {"e": "people.person_merged"}).first() is None:
        bind.execute(sa.text(
            "INSERT INTO domain_event_contracts "
            "(event_type, category, name, description, status, schema_version, owner, producer, "
            " payload_schema, depends_on) VALUES "
            "(:e, 'people', 'Canonical person merged', :d, 'active', 1, 'people', 'people.merge', "
            " :ps, '[]')"),
            {"e": "people.person_merged",
             "d": "A duplicate canonical person was merged into a surviving person (MDM-1).",
             "ps": _json.dumps({"survivor_person_id": "int", "merged_person_id": "int"})})
    if bind.execute(sa.text(
            "SELECT id FROM domain_event_subscriptions WHERE event_type=:e AND consumer=:c"),
            {"e": "people.person_merged", "c": "analytics.projection"}).first() is None:
        bind.execute(sa.text(
            "INSERT INTO domain_event_subscriptions (event_type, consumer, status, owner, description) "
            "VALUES (:e, 'analytics.projection', 'active', 'people', :d)"),
            {"e": "people.person_merged",
             "d": "Dark-launched analytics projector consumes canonical person merges."})


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM domain_event_subscriptions WHERE event_type='people.person_merged'"))
    bind.execute(sa.text("DELETE FROM domain_event_contracts WHERE event_type='people.person_merged'"))
    op.drop_index("ix_person_merge_history_merged", table_name="person_merge_history")
    op.drop_index("ix_person_merge_history_survivor", table_name="person_merge_history")
    op.drop_table("person_merge_history")
