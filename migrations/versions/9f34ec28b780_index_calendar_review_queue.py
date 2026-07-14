"""index calendar review queue

Revision ID: 9f34ec28b780
Revises: 0e62f932fe88
Create Date: 2026-07-13 21:15:00
"""

from typing import Sequence, Union

from alembic import op


revision: str = "9f34ec28b780"
down_revision: Union[str, Sequence[str], None] = "0e62f932fe88"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_microsoft_calendar_review_status_start",
        "microsoft_unmatched_calendar_attendees",
        ["status", "starts_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_microsoft_calendar_review_status_start",
        table_name="microsoft_unmatched_calendar_attendees",
    )
