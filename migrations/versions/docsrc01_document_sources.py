"""document_sources — one canonical document, many source references (ADR-072)

The additive migration ADR-072 anticipated: introduces ``document_sources`` so a single canonical
``documents`` row can be referenced by many source systems (TaxDome, Drake, SharePoint, Schwab,
AssetMark, Microsoft 365, Upload, Scanner, Email). Backfills one source reference per existing synced
document from its ``tags`` + ``sha256`` — no documents rows are moved or duplicated; ``documents.id``
stays the canonical id. No ADR change; no change to the RBAC/ownership model.

Revision ID: docsrc01
Revises: prodrolelib01
Create Date: 2026-08-02
"""
import sqlalchemy as sa
from alembic import op

revision = "docsrc01"
down_revision = "prodrolelib01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_sources",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer,
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("source_system", sa.Text, nullable=False),
        sa.Column("source_uri", sa.Text, nullable=False, server_default=""),
        sa.Column("source_path", sa.Text),
        sa.Column("source_external_id", sa.Text),
        sa.Column("source_hash", sa.String(64)),
        sa.Column("available", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("first_seen_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("last_synced_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("metadata", sa.dialects.postgresql.JSONB, nullable=False, server_default="{}"),
        sa.UniqueConstraint("document_id", "source_system", "source_uri",
                            name="uq_document_source_ref"),
    )
    op.create_index("ix_document_sources_hash", "document_sources", ["source_hash"])
    op.create_index("ix_document_sources_system", "document_sources", ["source_system"])
    # Backfill: one source reference per existing synced document, from its tags + hash.
    op.execute(sa.text("""
        INSERT INTO document_sources
            (document_id, source_system, source_uri, source_path, source_hash, last_synced_at, metadata)
        SELECT d.id,
               d.tags->>'source_system',
               COALESCE(d.storage_uri, d.tags->>'source_path', d.storage_path, ''),
               COALESCE(d.tags->>'source_path', d.storage_path),
               d.sha256,
               now(),
               '{}'::jsonb
        FROM documents d
        WHERE d.tags->>'source_system' IS NOT NULL
        ON CONFLICT ON CONSTRAINT uq_document_source_ref DO NOTHING
    """))


def downgrade() -> None:
    op.drop_index("ix_document_sources_system", table_name="document_sources")
    op.drop_index("ix_document_sources_hash", table_name="document_sources")
    op.drop_table("document_sources")
