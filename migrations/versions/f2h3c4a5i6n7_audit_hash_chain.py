"""Audit hash-chain columns (F3.2 / Epic 3, ADR-015 Option A).

Additive and reversible. Adds nullable hash-chain columns to ``audit_events`` so
the append-only audit log (F3.1) becomes tamper-evident. Existing rows are
untouched and remain valid but **unchained** (all hash columns NULL) — no
backfill, per ADR-015. The first record written after this migration becomes the
documented genesis of its chain. No change to the append-only trigger. Single
Alembic head preserved.
"""
import sqlalchemy as sa
from alembic import op

revision = "f2h3c4a5i6n7"
down_revision = "e1m2o3x4b5t6"
branch_labels = None
depends_on = None


def upgrade():
    # Nullable, no server_default: existing rows stay NULL (unchained); new rows
    # are populated by write_audit_event.
    op.add_column("audit_events", sa.Column("prev_hash", sa.Text(), nullable=True))
    op.add_column("audit_events", sa.Column("entry_hash", sa.Text(), nullable=True))
    op.add_column("audit_events", sa.Column("hash_version", sa.Integer(), nullable=True))
    op.add_column("audit_events", sa.Column("chain_id", sa.Text(), nullable=True))
    # Walk a chain in order.
    op.create_index("ix_audit_events_chain", "audit_events", ["chain_id", "id"])
    # Entry hashes are unique among chained rows (legacy NULLs excluded).
    op.create_index(
        "uq_audit_events_entry_hash", "audit_events", ["entry_hash"],
        unique=True, postgresql_where=sa.text("entry_hash IS NOT NULL"),
    )


def downgrade():
    op.drop_index("uq_audit_events_entry_hash", table_name="audit_events")
    op.drop_index("ix_audit_events_chain", table_name="audit_events")
    op.drop_column("audit_events", "chain_id")
    op.drop_column("audit_events", "hash_version")
    op.drop_column("audit_events", "entry_hash")
    op.drop_column("audit_events", "prev_hash")
