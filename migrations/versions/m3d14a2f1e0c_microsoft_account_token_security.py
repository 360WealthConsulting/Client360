"""microsoft account token security: encrypted cache and sync-health columns

Revision ID: m3d14a2f1e0c
Revises: l2c03f1e0d9b

Release 0.9.9 (Platform Consolidation), Phase 1. Adds an encrypted MSAL token
cache column (replacing reliance on the plaintext access/refresh token columns,
RC8/RC9 H10) and per-account sync-health columns. The legacy plaintext token
columns are left nullable for one release to allow safe rollback; their removal
is scheduled for a later release. Additive and reversible; existing connected
accounts must re-connect once (no usable refresh token exists today).
"""
from alembic import op
import sqlalchemy as sa

revision = "m3d14a2f1e0c"
down_revision = "l2c03f1e0d9b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("microsoft_accounts", sa.Column("token_cache_encrypted", sa.Text()))
    op.add_column("microsoft_accounts", sa.Column("last_sync_at", sa.DateTime(timezone=True)))
    op.add_column("microsoft_accounts", sa.Column("last_sync_status", sa.String(20)))
    op.add_column("microsoft_accounts", sa.Column("last_sync_error", sa.Text()))


def downgrade():
    op.drop_column("microsoft_accounts", "last_sync_error")
    op.drop_column("microsoft_accounts", "last_sync_status")
    op.drop_column("microsoft_accounts", "last_sync_at")
    op.drop_column("microsoft_accounts", "token_cache_encrypted")
