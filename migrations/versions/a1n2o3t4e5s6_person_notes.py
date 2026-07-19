"""Person notes — permanent client note + typed append-only notes (Sprint 1, Task 3).

Two database-backed models:

- ``person_permanent_notes`` — ONE editable long-lived note per person (enduring CRM facts,
  preferences, planning context). Edits are audited via the append-only audit trail; no separate
  version-history table. Legacy ``notes/{id}.txt`` blobs migrate here (idempotent).
- ``person_notes`` — append-only, author-attributed, timestamped entries with a ``note_type``
  (``note``/``call``/``meeting``/``email``/``task``/``system``). Activity notes today; Task 5
  call-logging and later communication features reuse this same table rather than adding new ones.

Additive and reversible; reflected at runtime (not declared in schema.py). No existing table is
altered. Filesystem migration is performed by the notes service (idempotent), not here.
"""
import sqlalchemy as sa
from alembic import op

revision = "a1n2o3t4e5s6"
down_revision = "f55d1s2p3t4c"
branch_labels = None
depends_on = None


def upgrade():
    names = set(sa.inspect(op.get_bind()).get_table_names())

    # 1. Permanent client note — one editable record per person (audited via audit_events).
    if "person_permanent_notes" not in names:
        op.create_table(
            "person_permanent_notes",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("person_id", sa.BigInteger(), nullable=False),
            sa.Column("body", sa.Text(), nullable=False, server_default=""),
            sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
            sa.Column("source", sa.String(length=40), server_default="staff", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("person_id", name="uq_person_permanent_note_person"),
        )

    # 2. Append-only typed person notes — the shared timeline-note table (reused by Task 5+).
    if "person_notes" not in names:
        op.create_table(
            "person_notes",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("person_id", sa.BigInteger(), nullable=False),
            sa.Column("author_user_id", sa.BigInteger(), nullable=True),
            sa.Column("note_type", sa.String(length=40), server_default="note", nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_person_notes_person ON person_notes (person_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_person_notes_type ON person_notes (person_id, note_type)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_person_notes_type")
    op.execute("DROP INDEX IF EXISTS ix_person_notes_person")
    op.execute("DROP TABLE IF EXISTS person_notes")
    op.execute("DROP TABLE IF EXISTS person_permanent_notes")
