"""add Microsoft document intelligence

Revision ID: 27bc61f29a81
Revises: 753c04edab33
Create Date: 2026-07-13 22:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "27bc61f29a81"
down_revision: Union[str, Sequence[str], None] = "753c04edab33"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "microsoft_drives",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("microsoft_drive_id", sa.String(length=500), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=True),
        sa.Column("drive_type", sa.String(length=100), nullable=True),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("site_id", sa.String(length=500), nullable=True),
        sa.Column("web_url", sa.Text(), nullable=True),
        sa.Column("delta_link", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("microsoft_drive_id"),
    )
    op.create_table(
        "microsoft_document_matching_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("rule_type", sa.String(length=50), nullable=False),
        sa.Column("pattern", sa.String(length=500), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("person_id", "rule_type", "pattern", name="uq_microsoft_document_matching_rule"),
    )
    op.create_table(
        "microsoft_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("microsoft_drive_id", sa.String(length=500), nullable=False),
        sa.Column("microsoft_item_id", sa.String(length=500), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("web_url", sa.Text(), nullable=True),
        sa.Column("parent_path", sa.Text(), nullable=True),
        sa.Column("created_at_microsoft", sa.DateTime(timezone=True), nullable=True),
        sa.Column("modified_at_microsoft", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_email", sa.String(length=320), nullable=True),
        sa.Column("modified_by_email", sa.String(length=320), nullable=True),
        sa.Column("match_method", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=50), server_default="pending", nullable=False),
        sa.Column("deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("raw_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("microsoft_drive_id", "microsoft_item_id", name="uq_microsoft_document_drive_item"),
    )
    op.create_index(
        "ix_microsoft_documents_review",
        "microsoft_documents",
        ["status", "deleted", "modified_at_microsoft"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_microsoft_documents_review", table_name="microsoft_documents")
    op.drop_table("microsoft_documents")
    op.drop_table("microsoft_document_matching_rules")
    op.drop_table("microsoft_drives")
