"""document_ocr — extracted text + OCR state against the canonical document (ADR-072)

OCR enriches the EXISTING canonical ``documents`` row; it does not introduce a second document system.
``document_ocr`` holds one row per canonical document: its extracted text, the engine + page/char
counts, and a small state machine (pending → processing → completed / failed / skipped) with an attempts
counter for retry, plus ``source_hash`` (the SHA-256 that was OCR'd) so a changed canonical document can
be reprocessed. The authoritative per-document status is mirrored onto ``documents.ocr_status`` (an
existing column) so the Documents tab and search read it directly. No ADR change; no ownership change.

Revision ID: dococr01
Revises: docsrc01
Create Date: 2026-08-02
"""
import sqlalchemy as sa
from alembic import op

revision = "dococr01"
down_revision = "docsrc01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_ocr",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer,
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("text", sa.Text),
        sa.Column("char_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("page_count", sa.Integer),
        sa.Column("engine", sa.Text),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text),
        sa.Column("source_hash", sa.String(64)),
        sa.Column("ocr_started_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("ocr_completed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('pending','processing','completed','failed','skipped','unsupported')",
            name="ck_document_ocr_status"),
        sa.UniqueConstraint("document_id", name="uq_document_ocr_document"),
    )
    op.create_index("ix_document_ocr_status", "document_ocr", ["status"])


def downgrade() -> None:
    op.drop_index("ix_document_ocr_status", table_name="document_ocr")
    op.drop_table("document_ocr")
