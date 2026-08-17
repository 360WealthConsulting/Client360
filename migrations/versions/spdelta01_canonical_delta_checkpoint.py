"""microsoft_drives — separate canonical SharePoint delta checkpoint from the legacy source-sync one

``microsoft_drives.delta_link`` is owned by the pre-existing ``microsoft_document_sync`` job (populates the
``microsoft_documents``/tax path). The NEW canonical SharePoint downloader (``run_sharepoint_delta_sync``)
must NOT reuse that column: a populated legacy ``delta_link`` would make the canonical sync resume from the
old job's checkpoint and skip its own initial baseline import. This adds a dedicated, independent canonical
checkpoint (``canonical_delta_link`` + ``canonical_delta_synced_at``) so the two workflows never collide.
Additive and reversible; the legacy ``delta_link`` is never touched.

Revision ID: spdelta01
Revises: dococr02
Create Date: 2026-08-16
"""
import sqlalchemy as sa
from alembic import op

revision = "spdelta01"
down_revision = "dococr02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("microsoft_drives", sa.Column("canonical_delta_link", sa.Text(), nullable=True))
    op.add_column("microsoft_drives",
                  sa.Column("canonical_delta_synced_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("microsoft_drives", "canonical_delta_synced_at")
    op.drop_column("microsoft_drives", "canonical_delta_link")
