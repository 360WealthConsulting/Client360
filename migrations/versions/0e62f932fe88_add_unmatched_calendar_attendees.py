"""add unmatched calendar attendees

Revision ID: 0e62f932fe88
Revises: 753c04edab33
Create Date: 2026-07-13 20:50:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0e62f932fe88"
down_revision: Union[str, Sequence[str], None] = "753c04edab33"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "microsoft_unmatched_calendar_attendees",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("microsoft_event_id", sa.String(length=500), nullable=False),
        sa.Column("attendee_email", sa.String(length=320), nullable=False),
        sa.Column("attendee_name", sa.String(length=255), nullable=True),
        sa.Column("attendee_role", sa.String(length=50), nullable=True),
        sa.Column("response_status", sa.String(length=50), nullable=True),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("location", sa.String(length=500), nullable=True),
        sa.Column("online_meeting_link", sa.Text(), nullable=True),
        sa.Column("web_link", sa.Text(), nullable=True),
        sa.Column("event_metadata", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=50),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("matched_person_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["matched_person_id"],
            ["people.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "microsoft_event_id",
            "attendee_email",
            name="uq_microsoft_calendar_event_attendee",
        ),
    )


def downgrade() -> None:
    op.drop_table("microsoft_unmatched_calendar_attendees")
