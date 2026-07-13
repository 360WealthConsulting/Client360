"""Baseline existing Client360 schema

Revision ID: 54fd91b24da6
Revises: 53802af14074
Create Date: 2026-07-12 23:16:19.180128

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '54fd91b24da6'
down_revision: Union[str, Sequence[str], None] = '53802af14074'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
