"""tax document links: dual source (canonical/microsoft) and unmatched support

Revision ID: l2c03f1e0d9b
Revises: k1b92e0d9c8a

Sprint 5.4 RC11 remediation. A tax document link may now reference EITHER a
canonical document OR a Microsoft document (the two ingestion sources), and an
unmatched link may have no return so unmatched documents can be persisted in a
reviewable state without fabricating ownership (RC11 C1/C5). Adds a partial
unique index preventing duplicate accepted links per Microsoft document/return.
"""
from alembic import op
import sqlalchemy as sa

revision = "l2c03f1e0d9b"
down_revision = "k1b92e0d9c8a"
branch_labels = None
depends_on = None


def upgrade():
    # Unmatched links carry no return; ambiguous/unmatched documents are stored
    # reviewable without inventing a return (RC11 C1/C5).
    op.alter_column("tax_document_links", "tax_engagement_return_id", nullable=True)
    # Second ingestion source: Microsoft drive documents, without duplicating the
    # binary as a canonical document (RC11 C5).
    op.add_column("tax_document_links",
        sa.Column("microsoft_document_id", sa.Integer(), sa.ForeignKey("microsoft_documents.id", ondelete="CASCADE")))
    op.alter_column("tax_document_links", "document_id", nullable=True)
    op.create_check_constraint("ck_tax_document_link_one_source", "tax_document_links",
        "num_nonnulls(document_id, microsoft_document_id) = 1")
    op.create_index("ix_tax_document_links_msdoc", "tax_document_links", ["microsoft_document_id"])
    # At most one accepted link per (microsoft document, return), mirroring the
    # canonical-document guard.
    op.create_index("uq_tax_document_link_ms_accepted", "tax_document_links",
        ["microsoft_document_id", "tax_engagement_return_id"], unique=True,
        postgresql_where=sa.text("status = 'accepted'"))


def downgrade():
    op.drop_index("uq_tax_document_link_ms_accepted", table_name="tax_document_links")
    op.drop_index("ix_tax_document_links_msdoc", table_name="tax_document_links")
    op.drop_constraint("ck_tax_document_link_one_source", "tax_document_links", type_="check")
    # Restore NOT NULL on document_id and tax_engagement_return_id; rows relying on
    # the relaxed shape (microsoft-sourced or unmatched links) are removed first.
    op.execute("DELETE FROM tax_document_links WHERE document_id IS NULL OR tax_engagement_return_id IS NULL")
    op.drop_column("tax_document_links", "microsoft_document_id")
    op.alter_column("tax_document_links", "document_id", nullable=False)
    op.alter_column("tax_document_links", "tax_engagement_return_id", nullable=False)
