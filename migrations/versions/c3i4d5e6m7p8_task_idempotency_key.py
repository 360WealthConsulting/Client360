"""Task submission idempotency key (Sprint 2).

Adds a nullable ``tasks.idempotency_key`` with a unique index so a resubmitted create-task form
(browser back/resubmit, double-click, retried POST) inserts conflict-safely and never produces a
duplicate task. NULLs remain distinct in Postgres, so existing and keyless tasks are unaffected.

Additive and reversible; the column is declared in schema.py so the declared and live schemas
stay consistent.
"""
import sqlalchemy as sa
from alembic import op

revision = "c3i4d5e6m7p8"
down_revision = "b2s3e4a5r6c7"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("tasks")}
    if "idempotency_key" not in columns:
        op.add_column("tasks", sa.Column("idempotency_key", sa.String(length=64), nullable=True))
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_idempotency_key "
        "ON tasks (idempotency_key)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS uq_tasks_idempotency_key")
    op.drop_column("tasks", "idempotency_key")
