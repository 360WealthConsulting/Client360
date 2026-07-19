"""Canonical notification ledger (F5.1 / Epic 5, ADR-017).

Additive and reversible. Creates the canonical, platform-level ``notifications``
delivery ledger — a non-authoritative record of **notification intent and delivery
outcomes only** (never authoritative for workflow, domain, or evidence state). It
generalizes recipients (portal account / staff user / team / ops) and sources
(event id / domain-workflow ref) beyond the portal-scoped ``portal_notifications``,
which is **left untouched** (no data migrated, deleted, or renamed).

The ledger row is intentionally **mutable** for its own lifecycle (status +
outcome timestamps + retry metadata) — so, unlike ``evidence``/``audit_events``,
there is **no** immutability trigger. Per-attempt append-only delivery history is a
future F5.5 concern (an additive ``notification_delivery_attempts`` table) and needs
no destructive redesign: the ``attempts``/``last_error`` and status-timestamp columns
already make F5.5 forward-compatible.

Content/reference boundary (ADR-017 §14): recipient-facing ``title``/``body`` content
lives only in this ledger; ``notification_metadata`` carries references only. F5.1 adds
no providers, dispatch, consumers, preferences, or routes.

Idempotent DDL (IF NOT EXISTS / IF EXISTS), consistent with the F3.2/F3.3/F4.1
migrations. The table is intentionally NOT declared in schema.py; app.db reflects it
at runtime (reflection compatibility; see docs/DATABASE.md).
"""
import sqlalchemy as sa
from alembic import op

revision = "f51n0t1c3d4e"
down_revision = "f41b2n3d4c5e"
branch_labels = None
depends_on = None

_STATUSES = "'pending','suppressed','delivered','disabled','failed','dead'"


def upgrade():
    if "notifications" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "notifications",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("notification_uid", sa.String(length=36), nullable=False),
            sa.Column("recipient_type", sa.String(length=50), nullable=False),
            sa.Column("recipient_ref", sa.String(length=255), nullable=False),
            sa.Column("channel", sa.String(length=30), nullable=False),
            sa.Column("notification_type", sa.String(length=150), nullable=False),
            sa.Column("status", sa.String(length=30), server_default="pending", nullable=False),
            sa.Column("dedupe_key", sa.String(length=255), nullable=False),
            sa.Column("source_event_id", sa.String(length=36), nullable=True),
            sa.Column("source_ref", sa.String(length=255), nullable=True),
            sa.Column("provider_ref", sa.String(length=150), nullable=True),
            sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("body", sa.Text(), nullable=True),
            sa.Column("notification_metadata", sa.JSON(), server_default="{}", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("suppressed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("notification_uid", name="uq_notifications_uid"),
            sa.UniqueConstraint("dedupe_key", name="uq_notifications_dedupe_key"),
            sa.CheckConstraint(f"status IN ({_STATUSES})", name="ck_notifications_status"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_notifications_recipient ON notifications (recipient_type, recipient_ref)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_notifications_status ON notifications (status, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_notifications_source_event ON notifications (source_event_id)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_notifications_source_event")
    op.execute("DROP INDEX IF EXISTS ix_notifications_status")
    op.execute("DROP INDEX IF EXISTS ix_notifications_recipient")
    op.execute("DROP TABLE IF EXISTS notifications")
