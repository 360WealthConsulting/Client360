"""Notification preferences & consent (F5.3 / Epic 5, ADR-017).

Additive and reversible. Creates two **separate** canonical, non-authoritative decision
inputs — ``notification_preferences`` (how a recipient wishes to be contacted) and
``notification_consents`` (whether communication is legally/operationally permitted).
They are deliberately distinct records/tables: a preference is never proof of consent.
Neither is authoritative for workflow, domain, or evidence state; they inform the F5.3
delivery **decision** only. There was no pre-existing preference/consent model in the
repository (documented in docs/NOTIFICATION_PREFERENCES.md).

Both rows are **current-state** (mutable for their own lifecycle: state + timestamps).
Consent withdrawal is represented by ``consent_state='withdrawn'`` + ``revoked_at`` — it
is **never** a row delete. Append-only per-change history is a future F5.6 concern (an
additive history table) and needs no destructive redesign — the ``*_uid``, timestamps,
and scope-unique rows already make it forward-compatible.

Reference-only: recipient/source/authority are references; **no** notification content
(title/body) is stored. Idempotent DDL; tables reflected at runtime (not declared in
schema.py). No provider is enabled; email/SMS/push remain disabled.
"""
import sqlalchemy as sa
from alembic import op

revision = "f53p1r2c3n4t"
down_revision = "f51n0t1c3d4e"
branch_labels = None
depends_on = None

_PREF_STATES = "'opted_in','opted_out','default'"
_CONSENT_STATES = "'granted','withdrawn'"


def upgrade():
    names = set(sa.inspect(op.get_bind()).get_table_names())
    if "notification_preferences" not in names:
        op.create_table(
            "notification_preferences",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("preference_uid", sa.String(length=36), nullable=False),
            sa.Column("recipient_type", sa.String(length=50), nullable=False),
            sa.Column("recipient_ref", sa.String(length=255), nullable=False),
            sa.Column("channel", sa.String(length=30), server_default="*", nullable=False),
            sa.Column("purpose", sa.String(length=150), server_default="*", nullable=False),
            sa.Column("preference_state", sa.String(length=30), server_default="default", nullable=False),
            sa.Column("source_ref", sa.String(length=255), nullable=True),
            sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("preference_uid", name="uq_notification_pref_uid"),
            sa.UniqueConstraint("recipient_type", "recipient_ref", "channel", "purpose", name="uq_notification_pref_scope"),
            sa.CheckConstraint(f"preference_state IN ({_PREF_STATES})", name="ck_notification_pref_state"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_notification_pref_recipient ON notification_preferences (recipient_type, recipient_ref)")

    if "notification_consents" not in names:
        op.create_table(
            "notification_consents",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("consent_uid", sa.String(length=36), nullable=False),
            sa.Column("recipient_type", sa.String(length=50), nullable=False),
            sa.Column("recipient_ref", sa.String(length=255), nullable=False),
            sa.Column("channel", sa.String(length=30), server_default="*", nullable=False),
            sa.Column("purpose", sa.String(length=150), server_default="*", nullable=False),
            sa.Column("consent_state", sa.String(length=30), nullable=False),
            sa.Column("authority_ref", sa.String(length=255), nullable=True),
            sa.Column("source_ref", sa.String(length=255), nullable=True),
            sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("consent_uid", name="uq_notification_consent_uid"),
            sa.UniqueConstraint("recipient_type", "recipient_ref", "channel", "purpose", name="uq_notification_consent_scope"),
            sa.CheckConstraint(f"consent_state IN ({_CONSENT_STATES})", name="ck_notification_consent_state"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_notification_consent_recipient ON notification_consents (recipient_type, recipient_ref)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_notification_consent_recipient")
    op.execute("DROP TABLE IF EXISTS notification_consents")
    op.execute("DROP INDEX IF EXISTS ix_notification_pref_recipient")
    op.execute("DROP TABLE IF EXISTS notification_preferences")
