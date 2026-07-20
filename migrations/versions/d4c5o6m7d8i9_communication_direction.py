"""Communication direction on person notes (Sprint 2).

Adds an optional ``direction`` (inbound/outbound) to ``person_notes`` so logged communications
record whether the client contacted the firm or vice versa. Null for general activity notes.
Additive and reversible; ``person_notes`` is a reflection-only table, so no schema.py change.
"""
import sqlalchemy as sa
from alembic import op

revision = "d4c5o6m7d8i9"
down_revision = "c3i4d5e6m7p8"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("person_notes")}
    if "direction" not in columns:
        op.add_column("person_notes", sa.Column("direction", sa.String(length=20), nullable=True))


def downgrade():
    op.drop_column("person_notes", "direction")
