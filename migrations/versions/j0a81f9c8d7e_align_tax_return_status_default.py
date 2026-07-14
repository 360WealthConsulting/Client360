"""align tax return status default with lifecycle vocabulary

Revision ID: j0a81f9c8d7e
Revises: i970d9f7b8c9

Release 0.9.7 security hardening (H11): the tax return lifecycle vocabulary
begins at ``received`` and no longer includes ``not_started``. The column's
server default was left at the pre-lifecycle value ``not_started`` (a state the
lifecycle state machine can never transition out of), which is a latent
schema-drift trap for any direct/bulk insert that omits ``status``. This
migration aligns the default and normalizes any residual rows.
"""
from alembic import op

revision = "j0a81f9c8d7e"
down_revision = "i970d9f7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("tax_engagement_returns", "status", server_default="received")
    op.execute("UPDATE tax_engagement_returns SET status='received' WHERE status='not_started'")


def downgrade():
    op.alter_column("tax_engagement_returns", "status", server_default="not_started")
