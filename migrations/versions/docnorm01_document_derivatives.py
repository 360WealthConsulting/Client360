"""document_derivatives — normalized image renditions of the canonical document (ADR-072)

Normalization ENRICHES the existing canonical ``documents`` row; it does not introduce a second
document system. The uploaded original is never replaced — it stays exactly as uploaded, with its own
``original_name``, ``content_type``, storage path and ``sha256``. ``document_derivatives`` holds one row
per (document, derivative kind) recording the derivative Client360 produced from it: the source MIME +
hash it was made from, the derivative MIME + path + hash + pixel size, the engine, and the same small
state machine ``document_ocr`` uses (pending -> processing -> completed / failed / skipped /
unsupported) with an attempts counter for retry.

Today the only kind is ``normalized_image`` — the JPEG rendition of an iPhone HEIC/HEIF upload that
OCR, the browser preview and every image-consuming AI call use in place of the HEIF original. The
per-document status is mirrored onto ``documents.preview_status`` (an existing, previously unwritten
column) so document lists can read it without a join.

Deletion behavior is EXPLICIT: ``document_id`` is ``ON DELETE CASCADE``, matching ``document_ocr``
and every other per-document side table — permanently deleting a document takes its derivative rows
with it and can never leave a dangling reference. It cannot cause an original to be deleted (this
table holds no original), and the physical derivative FILE is content-addressed and swept separately
by ``document_derivatives.prune_orphan_derivatives``, which only ever removes a generated ``<sha>.jpg``
no live document can still claim.

No ADR change; no ownership change; no change to how or where originals are stored.

Revision ID: docnorm01
Revises: msgcap01
Create Date: 2026-09-01
"""
import sqlalchemy as sa
from alembic import op

revision = "docnorm01"
down_revision = "msgcap01"
branch_labels = None
depends_on = None

STATUSES = ("pending", "processing", "completed", "failed", "skipped", "unsupported")


def upgrade() -> None:
    op.create_table(
        "document_derivatives",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer,
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        # The derivative's purpose. One row per (document, kind) so a future kind (e.g. a thumbnail)
        # is additive rather than a second table.
        sa.Column("kind", sa.Text, nullable=False, server_default="normalized_image"),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        # Provenance of the ORIGINAL this derivative was made from.
        sa.Column("source_mime", sa.Text),
        sa.Column("source_hash", sa.String(64)),
        # Provenance of the DERIVATIVE itself.
        sa.Column("derivative_mime", sa.Text),
        sa.Column("derivative_path", sa.Text),
        sa.Column("derivative_hash", sa.String(64)),
        sa.Column("derivative_size_bytes", sa.BigInteger),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("engine", sa.Text),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text),
        sa.Column("converted_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("status IN (" + ",".join(f"'{s}'" for s in STATUSES) + ")",
                           name="ck_document_derivatives_status"),
        sa.UniqueConstraint("document_id", "kind", name="uq_document_derivative_kind"),
    )
    op.create_index("ix_document_derivatives_status", "document_derivatives", ["status"])
    op.create_index("ix_document_derivatives_hash", "document_derivatives", ["derivative_hash"])


def downgrade() -> None:
    op.drop_index("ix_document_derivatives_hash", table_name="document_derivatives")
    op.drop_index("ix_document_derivatives_status", table_name="document_derivatives")
    op.drop_table("document_derivatives")
