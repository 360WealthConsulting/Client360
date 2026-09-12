"""ocr_document_claims — per-document work claiming so several OCR workers can sweep the corpus safely

The single-worker runner serialises the whole corpus behind one session-level advisory lock held for
the worker's entire life (``worker.py`` key 511005777): a second copy exits immediately. That lock is
the only thing preventing two workers from processing the same document, and it costs all
parallelism to get it.

This table replaces that coarse lock with a per-document claim. A claim is taken by ONE statement
whose uniqueness is enforced by the primary key, so two workers racing for the same document cannot
both win. Each claim carries a LEASE; a worker that dies without releasing simply lets its lease
lapse and the document becomes claimable again, which is what makes crash recovery automatic rather
than a cleanup job.

Purely additive. No existing table is altered, no OCR result is touched, and the single-worker
runner keeps working unchanged if this table is never used: claiming is opt-in at the runner level.

Revision ID: ocrclaim01
Revises: docpipe02
Create Date: 2026-09-12
"""
import sqlalchemy as sa
from alembic import op

revision = "ocrclaim01"
down_revision = "docpipe02"
branch_labels = None
depends_on = None

_STATES = "('claimed','done','released')"


def upgrade() -> None:
    op.create_table(
        "ocr_document_claims",
        # One row per document: the PK is the mutual-exclusion primitive. Two concurrent claims for
        # the same document serialise on this index, so exactly one can win.
        sa.Column("document_id", sa.Integer,
                  sa.ForeignKey("documents.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("worker_id", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False, server_default="claimed"),
        # Bumped on every (re)claim. A result write carrying a stale claim_seq is from a worker whose
        # lease was already stolen, so the write can be recognised and ignored.
        sa.Column("claim_seq", sa.Integer, nullable=False, server_default="1"),
        sa.Column("claimed_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("heartbeat_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        # The lease. Past now() and still 'claimed' means the holder died or wedged; any worker may
        # take it over. Refreshed by the holder's heartbeat while it is genuinely working.
        sa.Column("lease_expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("outcome", sa.Text),
        sa.CheckConstraint(f"state IN {_STATES}", name="ck_ocr_document_claims_state"),
    )
    # The claim hot path: find rows that are reclaimable (expired lease, not done).
    op.create_index("ix_ocr_document_claims_lease", "ocr_document_claims",
                    ["state", "lease_expires_at"])
    op.create_index("ix_ocr_document_claims_worker", "ocr_document_claims", ["worker_id"])


def downgrade() -> None:
    op.drop_index("ix_ocr_document_claims_worker", table_name="ocr_document_claims")
    op.drop_index("ix_ocr_document_claims_lease", table_name="ocr_document_claims")
    op.drop_table("ocr_document_claims")
