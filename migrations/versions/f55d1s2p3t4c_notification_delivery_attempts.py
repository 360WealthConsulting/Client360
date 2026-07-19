"""Notification delivery attempts (F5.5 / Epic 5, ADR-017).

Additive and reversible. Adds the **execution** substrate for notification dispatch —
``notification_delivery_attempts``: an **immutable, append-only** record of each provider
dispatch attempt for a notification intent (one row per attempt; never updated or deleted —
an immutability trigger enforces this, matching ``audit_events`` / ``evidence``).
Reference-only: it stores provider/channel/timing/outcome references and a content-free
normalized result — **no** notification ``title``/``body`` or contact data. This is the
table F5.1 explicitly reserved as "a future F5.5 concern".

Architectural note (Model A): the notification ledger remains an **intent/disposition
ledger**. Transient provider behavior (provider unavailable, timeout, DNS failure, HTTP
429/503, connection reset, ...) belongs **exclusively** to this delivery-attempt history —
it never becomes a notification lifecycle status. This migration therefore does **not**
alter the ``notifications.status`` CHECK or lifecycle in any way; the notification status
vocabulary is unchanged from F5.4.

The delivery-attempt table is authoritative only for **execution history**; it is never
authoritative for workflow, domain, business-event, evidence, or eligibility state. Idempotent
DDL; tables reflected at runtime (not declared in schema.py). No provider is enabled.
"""
import sqlalchemy as sa
from alembic import op

revision = "f55d1s2p3t4c"
down_revision = "f53p1r2c3n4t"
branch_labels = None
depends_on = None

# Execution outcomes live in the attempt row (NOT in notification status). ``provider_unavailable``
# and other transient conditions are normalized execution results recorded here only.
_EXEC_RESULTS = "'delivered','failed','provider_unavailable'"
_PROVIDER_STATUS = "'delivered','disabled','failed'"


def upgrade():
    # immutable, append-only delivery-attempt history. The notifications.status CHECK is
    # intentionally left unchanged (Model A: no transient provider condition is a status).
    names = set(sa.inspect(op.get_bind()).get_table_names())
    if "notification_delivery_attempts" not in names:
        op.create_table(
            "notification_delivery_attempts",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("attempt_uid", sa.String(length=36), nullable=False),
            sa.Column("notification_id", sa.BigInteger(), nullable=False),
            sa.Column("notification_uid", sa.String(length=36), nullable=False),
            sa.Column("attempt_seq", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(length=100), nullable=False),
            sa.Column("channel", sa.String(length=30), nullable=False),
            sa.Column("execution_started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("execution_completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("provider_message_id", sa.String(length=150), nullable=True),
            sa.Column("provider_status", sa.String(length=30), nullable=True),
            sa.Column("execution_result", sa.String(length=30), nullable=False),
            sa.Column("retry_recommended", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("failure_class", sa.String(length=50), nullable=True),
            sa.Column("correlation_ref", sa.String(length=255), nullable=True),
            sa.Column("causation_ref", sa.String(length=255), nullable=True),
            sa.Column("attempt_metadata", sa.JSON(), server_default="{}", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("attempt_uid", name="uq_notif_attempt_uid"),
            sa.UniqueConstraint("notification_id", "attempt_seq", name="uq_notif_attempt_seq"),
            sa.CheckConstraint(f"execution_result IN ({_EXEC_RESULTS})", name="ck_notif_attempt_result"),
            sa.CheckConstraint(f"provider_status IN ({_PROVIDER_STATUS})", name="ck_notif_attempt_provider_status"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_notif_attempt_notification ON notification_delivery_attempts (notification_id, attempt_seq)")

    # append-only: block UPDATE and DELETE (same pattern as audit_events / evidence).
    op.execute("CREATE OR REPLACE FUNCTION prevent_notif_attempt_mutation() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'notification_delivery_attempts are append-only'; END; $$ LANGUAGE plpgsql")
    op.execute("DROP TRIGGER IF EXISTS notif_attempt_immutable ON notification_delivery_attempts")
    op.execute("CREATE TRIGGER notif_attempt_immutable BEFORE UPDATE OR DELETE ON notification_delivery_attempts FOR EACH ROW EXECUTE FUNCTION prevent_notif_attempt_mutation()")


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS notif_attempt_immutable ON notification_delivery_attempts")
    op.execute("DROP FUNCTION IF EXISTS prevent_notif_attempt_mutation()")
    op.execute("DROP INDEX IF EXISTS ix_notif_attempt_notification")
    op.execute("DROP TABLE IF EXISTS notification_delivery_attempts")
    # No notifications.status change was made on upgrade, so nothing to restore there.
