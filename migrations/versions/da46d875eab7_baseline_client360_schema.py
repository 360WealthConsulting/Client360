"""Baseline Client360 schema

Revision ID: da46d875eab7
Revises: 
Create Date: 2026-07-11 15:37:46.557202

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'da46d875eab7'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the original Client360 core schema.

    This revision was initially recorded as a stamp for a schema that had been
    created with SQLAlchemy metadata.  Keeping the DDL here makes the revision
    an actual baseline for new databases.  Databases already stamped at this
    revision or later do not rerun these operations.
    """
    op.create_table(
        "households",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("address_line_1", sa.String(length=255), nullable=True),
        sa.Column("address_line_2", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=50), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "source_contacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_system", sa.String(length=100), nullable=False),
        sa.Column("source_file", sa.String(length=500), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=True),
        sa.Column("middle_name", sa.String(length=100), nullable=True),
        sa.Column("last_name", sa.String(length=100), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("normalized_email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("normalized_phone", sa.String(length=30), nullable=True),
        sa.Column("address_line_1", sa.String(length=255), nullable=True),
        sa.Column("address_line_2", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=50), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("territory", sa.String(length=255), nullable=True),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_system",
            "source_hash",
            name="uq_source_contacts_system_hash",
        ),
    )
    op.create_index(
        op.f("ix_source_contacts_normalized_email"),
        "source_contacts",
        ["normalized_email"],
        unique=False,
    )
    op.create_index(
        op.f("ix_source_contacts_normalized_phone"),
        "source_contacts",
        ["normalized_phone"],
        unique=False,
    )
    op.create_table(
        "people",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("household_id", sa.Integer(), nullable=True),
        sa.Column("first_name", sa.String(length=100), nullable=True),
        sa.Column("middle_name", sa.String(length=100), nullable=True),
        sa.Column("last_name", sa.String(length=100), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("preferred_name", sa.String(length=100), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("primary_email", sa.String(length=255), nullable=True),
        sa.Column("normalized_email", sa.String(length=255), nullable=True),
        sa.Column("primary_phone", sa.String(length=50), nullable=True),
        sa.Column("normalized_phone", sa.String(length=30), nullable=True),
        sa.Column("address_line_1", sa.String(length=255), nullable=True),
        sa.Column("address_line_2", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=50), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("contact_type", sa.String(length=100), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_people_normalized_email"),
        "people",
        ["normalized_email"],
        unique=False,
    )
    op.create_index(
        op.f("ix_people_normalized_phone"),
        "people",
        ["normalized_phone"],
        unique=False,
    )
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=True),
        sa.Column("household_id", sa.Integer(), nullable=True),
        sa.Column("custodian", sa.String(length=100), nullable=False),
        sa.Column("account_number", sa.String(length=100), nullable=True),
        sa.Column("account_name", sa.String(length=255), nullable=True),
        sa.Column("registration_type", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=100), nullable=True),
        sa.Column("total_value", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("cash_value", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("open_date", sa.Date(), nullable=True),
        sa.Column("closed_date", sa.Date(), nullable=True),
        sa.Column("source_file", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"]),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "custodian",
            "account_number",
            name="uq_account_custodian_number",
        ),
    )
    op.create_table(
        "person_source_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("source_contact_id", sa.Integer(), nullable=False),
        sa.Column("match_method", sa.String(length=100), nullable=True),
        sa.Column("match_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["source_contact_id"], ["source_contacts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "person_id",
            "source_contact_id",
            name="uq_person_source_link",
        ),
    )


def downgrade() -> None:
    """Remove the original Client360 core schema."""
    op.drop_table("person_source_links")
    op.drop_table("accounts")
    op.drop_index(op.f("ix_people_normalized_phone"), table_name="people")
    op.drop_index(op.f("ix_people_normalized_email"), table_name="people")
    op.drop_table("people")
    op.drop_index(
        op.f("ix_source_contacts_normalized_phone"),
        table_name="source_contacts",
    )
    op.drop_index(
        op.f("ix_source_contacts_normalized_email"),
        table_name="source_contacts",
    )
    op.drop_table("source_contacts")
    op.drop_table("households")
