"""Transactional outbox & dispatcher tables (E1.6 / Backlog F1.3).

Adds generic, additive infrastructure for reliable event publication:
  * outbox_events           — events written in the producer's transaction (atomic)
  * outbox_dead_letters     — events that exhausted their retries
  * outbox_processed_events — consumer idempotency ledger (event_id, consumer)

Additive and reversible. No existing table or data is modified. Single Alembic
head preserved (down_revision is the prior head). Hand-written per the migration
standard in docs/DATABASE.md (autogenerate is unsafe against the partial
target_metadata).
"""
import sqlalchemy as sa
from alembic import op

revision = "e1m2o3x4b5t6"
down_revision = "d0l1n2o3i4k5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("payload", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_outbox_events_event_id"),
    )
    op.create_index(
        "ix_outbox_events_status_available", "outbox_events", ["status", "available_at"]
    )
    op.create_table(
        "outbox_dead_letters",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("payload", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "outbox_processed_events",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("consumer", sa.String(length=200), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("event_id", "consumer", name="pk_outbox_processed_events"),
    )


def downgrade():
    op.drop_table("outbox_processed_events")
    op.drop_table("outbox_dead_letters")
    op.drop_index("ix_outbox_events_status_available", table_name="outbox_events")
    op.drop_table("outbox_events")
