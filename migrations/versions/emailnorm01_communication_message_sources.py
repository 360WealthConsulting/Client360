"""Provider identity for communication messages — inbound Outlook email normalization (Batch 4a).

Inbound mail is ingested today (``app/jobs/microsoft_mail_sync.py``) into ``timeline_events`` and,
when the sender is unrecognised, ``microsoft_unmatched_messages``. It creates no canonical
``communication_*`` record, and it discards the two Microsoft identifiers that make an email
addressable: ``internetMessageId`` and ``conversationId``.

Normalizing needs one thing the communications schema does not have: a UNIQUE provider identity.
Nothing in ``communication_*`` carries one — the only unique constraints are ``templates.code`` and
the two attachment pairs — so an idempotent ingest would have to read-then-write, which races between
workers. This adds that constraint.

SHAPE. ``communication_message_sources`` deliberately mirrors ``document_sources`` (``docsrc01``,
ADR-072), whose source-system list already names "Email": one canonical record, many source
references. That gives multi-mailbox for free — the same message seen in two connected mailboxes is
ONE ``communication_messages`` row with two source rows, not two messages.

IDENTITY. ``source_external_id`` holds the RFC 5322 ``internetMessageId``, which is globally unique
and stable across folders, mailboxes and tenants. The Graph ``id`` is NOT used as identity: it is
mailbox-scoped and changes when a message moves between folders, which is why the existing timeline
key (``outlook-message-{graph id}``) can record the same email twice. When a message carries no
Message-ID (drafts, some calendar-generated items) the caller falls back to a composite
``{tenant}:{mailbox}:{graph id}``; the Graph id, mailbox and tenant are always kept in ``metadata``.

SENDER VOCABULARY. ``SENDER_TYPES`` was ``('user','system')``. Inbound mail has an EXTERNAL sender —
a client, not a staff user and not the platform — and recording that as ``system`` would be false in
the data, so the CHECK is replaced to admit ``external``. Additive: no existing row uses it, and
every existing value stays legal.

No backfill. No existing row is read, moved or deleted. Reversible.
"""
import sqlalchemy as sa
from alembic import op

revision = "emailnorm01"
down_revision = "vaultdl01"
branch_labels = None
depends_on = None

_CHECK = "ck_comm_message_sender_type"
_OLD_SENDER_TYPES = "'user','system'"
_NEW_SENDER_TYPES = "'user','system','external'"


def upgrade():
    op.create_table(
        "communication_message_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(),
                  sa.ForeignKey("communication_messages.id", ondelete="CASCADE"), nullable=False),
        # Which system this sighting came from, and its identifier THERE. The pair is the
        # idempotency key: re-reading the same message can only ever find the existing row.
        sa.Column("source_system", sa.Text(), nullable=False),
        sa.Column("source_external_id", sa.Text(), nullable=False),
        sa.Column("source_uri", sa.Text()),
        # References only — Graph id, mailbox and tenant so a sighting stays traceable after a move.
        sa.Column("source_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("source_system", "source_external_id",
                            name="uq_comm_message_source_identity"),
        sa.UniqueConstraint("message_id", "source_system", "source_external_id",
                            name="uq_comm_message_source_ref"),
    )
    op.create_index("ix_comm_message_source_message", "communication_message_sources",
                    ["message_id"])
    op.create_index("ix_comm_message_source_system", "communication_message_sources",
                    ["source_system"])

    # Admit an EXTERNAL sender. Drop-and-recreate is the only way to widen a CHECK; every existing
    # value remains legal, so no row can be invalidated by it.
    op.drop_constraint(_CHECK, "communication_messages", type_="check")
    op.create_check_constraint(_CHECK, "communication_messages",
                               f"sender_type IN ({_NEW_SENDER_TYPES})")


def downgrade():
    # Narrowing the vocabulary would orphan any external-sender row, so refuse rather than corrupt.
    bind = op.get_bind()
    external = bind.execute(sa.text(
        "SELECT count(*) FROM communication_messages WHERE sender_type = 'external'")).scalar()
    if external:
        raise RuntimeError(
            f"communication_messages holds {external} row(s) with sender_type='external'. "
            "Downgrading would leave them violating the narrowed CHECK. Re-classify or remove "
            "them deliberately, then downgrade again.")
    op.drop_constraint(_CHECK, "communication_messages", type_="check")
    op.create_check_constraint(_CHECK, "communication_messages",
                               f"sender_type IN ({_OLD_SENDER_TYPES})")

    op.drop_index("ix_comm_message_source_system", table_name="communication_message_sources")
    op.drop_index("ix_comm_message_source_message", table_name="communication_message_sources")
    op.drop_table("communication_message_sources")
