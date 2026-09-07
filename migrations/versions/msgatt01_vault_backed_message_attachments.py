"""Vault-backed message attachments — a second, nullable storage reference on both attachment tables.

WHY. ``portal_message_attachments`` and ``communication_attachments`` both reference the CANONICAL
``documents`` table, because both predate the Vault: the portal ships in ``f640a6c4e5f6`` and
communications in ``p6a7b8c9d0e1`` (the D.17 head), while ``vault_documents`` arrives in
``va01ult0mvp1`` on the D.63 head and ``client_visible`` later still in ``pv02rtl0vlt1``. Neither
table could reference a store that did not yet exist. Meanwhile every client-safe document surface is
vault-only by design (``de26702`` — "there is NO fallback to the canonical table"), so a client can
reach no ``documents`` row at all and message attachments were unreachable from the client side.

This adds the missing reference. It is additive: no column is renamed or dropped, no data is moved,
no row is deleted, and no backfill runs.

TWO TABLES, TWO CONSTRAINTS — because they have deliberately different lifecycles.

``portal_message_attachments``  document_id is NOT NULL with ON DELETE **CASCADE**: deleting the
    document deletes the attachment, so a row can never exist without a live reference. EXACTLY ONE
    reference is therefore both correct and permanently satisfiable, and the new vault FK is CASCADE
    for the same reason — a SET NULL there would manufacture the "neither" row that the exactly-one
    CHECK forbids, turning a document deletion into a constraint violation.

``communication_attachments``   document_id is already nullable with ON DELETE **SET NULL** (declared
    identically in the migration and in ``app/database/communication_tables.py``): deleting the
    document leaves the attachment row as a TOMBSTONE that preserves communication history. Every one
    of the existing rows is in exactly that state. AT MOST ONE is therefore the only constraint
    consistent with the table's own semantics, and the new vault FK is SET NULL to match. Forcing
    exactly-one here would outlaw a state D.18 chose on purpose and would require destroying history.

Not symmetric, deliberately. See ADR-072 (one canonical document) for the longer-term question of why
two document stores exist at all — that convergence is out of scope here and this change does not
obstruct it.

Reversible. The downgrade refuses rather than silently discarding vault-backed attachments.
"""
import sqlalchemy as sa
from alembic import op

revision = "msgatt01"
down_revision = "cf01"
branch_labels = None
depends_on = None

#: Exactly one reference. `<>` on two booleans is XOR: true when precisely one side is populated.
_PORTAL_CHECK = "(document_id IS NOT NULL) <> (vault_document_id IS NOT NULL)"
#: At most one reference. "Neither" stays legal — that is the tombstone state.
_COMM_CHECK = "NOT (document_id IS NOT NULL AND vault_document_id IS NOT NULL)"


def upgrade():
    # --- portal_message_attachments: exactly one, CASCADE both sides ---------------------------
    op.add_column("portal_message_attachments",
                  sa.Column("vault_document_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_pma_vault_document", "portal_message_attachments",
                          "vault_documents", ["vault_document_id"], ["id"], ondelete="CASCADE")
    # document_id must become nullable for a vault-only row to be representable. This is the one
    # non-additive step and it invalidates nothing: relaxing NOT NULL cannot reject an existing row.
    op.alter_column("portal_message_attachments", "document_id",
                    existing_type=sa.Integer(), nullable=True)
    op.create_index("ix_pma_vault_document_id", "portal_message_attachments",
                    ["vault_document_id"])
    op.create_unique_constraint("uq_portal_message_vault_attachment",
                                "portal_message_attachments", ["message_id", "vault_document_id"])
    # Added AFTER the column exists, so it is validated against rows that already satisfy it: every
    # current row has document_id set and vault_document_id NULL.
    op.create_check_constraint("ck_pma_exactly_one_reference",
                               "portal_message_attachments", _PORTAL_CHECK)

    # --- communication_attachments: at most one, SET NULL both sides ---------------------------
    op.add_column("communication_attachments",
                  sa.Column("vault_document_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_comm_attachment_vault_document", "communication_attachments",
                          "vault_documents", ["vault_document_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_comm_attachment_vault_document_id", "communication_attachments",
                    ["vault_document_id"])
    op.create_unique_constraint("uq_comm_attachment_vault_document",
                                "communication_attachments", ["message_id", "vault_document_id"])
    op.create_check_constraint("ck_comm_attachment_at_most_one_reference",
                               "communication_attachments", _COMM_CHECK)


def downgrade():
    # A vault-backed attachment cannot be represented once the column is gone. Dropping it would
    # silently destroy real links (and, on the portal table, leave rows that cannot satisfy the
    # restored NOT NULL), so refuse loudly instead — the operator decides what to do with them.
    bind = op.get_bind()
    for table in ("portal_message_attachments", "communication_attachments"):
        remaining = bind.execute(sa.text(
            f"SELECT count(*) FROM {table} WHERE vault_document_id IS NOT NULL")).scalar()
        if remaining:
            raise RuntimeError(
                f"{table} holds {remaining} vault-backed attachment(s). Downgrading would discard "
                "them. Re-point or remove those rows deliberately, then downgrade again.")

    op.drop_constraint("ck_comm_attachment_at_most_one_reference", "communication_attachments",
                       type_="check")
    op.drop_constraint("uq_comm_attachment_vault_document", "communication_attachments",
                       type_="unique")
    op.drop_index("ix_comm_attachment_vault_document_id", table_name="communication_attachments")
    op.drop_constraint("fk_comm_attachment_vault_document", "communication_attachments",
                       type_="foreignkey")
    op.drop_column("communication_attachments", "vault_document_id")

    op.drop_constraint("ck_pma_exactly_one_reference", "portal_message_attachments", type_="check")
    op.drop_constraint("uq_portal_message_vault_attachment", "portal_message_attachments",
                       type_="unique")
    op.drop_index("ix_pma_vault_document_id", table_name="portal_message_attachments")
    op.drop_constraint("fk_pma_vault_document", "portal_message_attachments", type_="foreignkey")
    op.drop_column("portal_message_attachments", "vault_document_id")
    # Safe: the guard above proved no vault-only row survives, so document_id is set on every row.
    op.alter_column("portal_message_attachments", "document_id",
                    existing_type=sa.Integer(), nullable=False)
