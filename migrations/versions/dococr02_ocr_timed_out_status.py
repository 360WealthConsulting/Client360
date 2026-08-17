"""document_ocr — allow the 'timed_out' status (bounded OCR that overran its page/document budget)

OCR is now bounded per page and per document so one problematic scanned PDF can never hang the ingestion
batch. A run that exceeds its time budget is recorded distinctly as ``timed_out`` (retryable, like
``failed``) rather than being conflated with a generic extraction failure. This widens the existing
``ck_document_ocr_status`` CHECK constraint to permit that value. Additive and reversible; no data change.

Revision ID: dococr02
Revises: pfm01
Create Date: 2026-08-16
"""
from alembic import op

revision = "dococr02"
down_revision = "pfm01"
branch_labels = None
depends_on = None

_OLD = "status IN ('pending','processing','completed','failed','skipped','unsupported')"
_NEW = "status IN ('pending','processing','completed','failed','timed_out','skipped','unsupported')"


def upgrade() -> None:
    op.drop_constraint("ck_document_ocr_status", "document_ocr", type_="check")
    op.create_check_constraint("ck_document_ocr_status", "document_ocr", _NEW)


def downgrade() -> None:
    # Fold any timed_out rows back to failed so the narrower constraint can be re-applied.
    op.execute("UPDATE document_ocr SET status = 'failed' WHERE status = 'timed_out'")
    op.drop_constraint("ck_document_ocr_status", "document_ocr", type_="check")
    op.create_check_constraint("ck_document_ocr_status", "document_ocr", _OLD)
