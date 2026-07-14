"""add relationship intelligence

Revision ID: 5bd72a4cc901
Revises: 753c04edab33
Create Date: 2026-07-13 23:45:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5bd72a4cc901"
down_revision: Union[str, Sequence[str], None] = "753c04edab33"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RELATIONSHIP_TYPES = [
    ("spouse", "Spouse", "Spouse", "family", False),
    ("child", "Child", "Parent", "family", True),
    ("parent", "Parent", "Child", "family", True),
    ("sibling", "Sibling", "Sibling", "family", False),
    ("business_partner", "Business Partner", "Business Partner", "business", False),
    ("owner", "Owner", "Owned By", "business", True),
    ("employee", "Employee", "Employer", "business", True),
    ("employer", "Employer", "Employee", "business", True),
    ("cpa", "CPA", "Client", "professional", True),
    ("attorney", "Attorney", "Client", "professional", True),
    ("insurance_agent", "Insurance Agent", "Client", "professional", True),
    ("banker", "Banker", "Client", "professional", True),
    ("financial_advisor", "Financial Advisor", "Client", "professional", True),
    ("trustee", "Trustee", "Trust", "estate", True),
    ("successor_trustee", "Successor Trustee", "Trust", "estate", True),
    ("executor", "Executor", "Estate", "estate", True),
    ("beneficiary", "Beneficiary", "Benefactor", "estate", True),
    ("power_of_attorney", "Power of Attorney", "Principal", "estate", True),
    ("client_referral", "Client Referral", "Referred By", "referral", True),
    ("emergency_contact", "Emergency Contact", "Contact For", "emergency", True),
    ("household_member", "Household Member", "Member", "household", True),
    ("buy_sell_agreement", "Buy-Sell Agreement", "Agreement For", "business", True),
]


def upgrade() -> None:
    op.add_column(
        "household_relationships",
        sa.Column(
            "is_primary_household",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    op.execute("""
        UPDATE household_relationships hr
        SET is_primary_household = true
        FROM people p
        WHERE p.id = hr.person_id AND p.household_id = hr.household_id
    """)
    op.create_index(
        "uq_household_primary_per_person",
        "household_relationships",
        ["person_id"],
        unique=True,
        postgresql_where=sa.text("is_primary_household = true"),
    )
    op.create_table(
        "relationship_types",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("inverse_name", sa.String(length=255), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("directed", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "relationship_entities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=True),
        sa.Column("household_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("details", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("household_id"),
        sa.UniqueConstraint("person_id"),
    )
    op.create_table(
        "relationships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("from_entity_id", sa.Integer(), nullable=False),
        sa.Column("to_entity_id", sa.Integer(), nullable=False),
        sa.Column("relationship_type_id", sa.Integer(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("inactive_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("confidence_level", sa.Numeric(5, 2), server_default="100", nullable=False),
        sa.Column("source", sa.String(length=50), server_default="manual", nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["from_entity_id"], ["relationship_entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["relationship_type_id"], ["relationship_types.id"]),
        sa.ForeignKeyConstraint(["to_entity_id"], ["relationship_entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("from_entity_id", "to_entity_id", "relationship_type_id", name="uq_relationship_edge"),
    )
    op.create_index("ix_relationships_from_active", "relationships", ["from_entity_id", "active"], unique=False)
    op.create_index("ix_relationships_to_active", "relationships", ["to_entity_id", "active"], unique=False)

    types_table = sa.table(
        "relationship_types",
        sa.column("code", sa.String), sa.column("name", sa.String),
        sa.column("inverse_name", sa.String), sa.column("category", sa.String),
        sa.column("directed", sa.Boolean),
    )
    op.bulk_insert(types_table, [
        {"code": code, "name": name, "inverse_name": inverse, "category": category, "directed": directed}
        for code, name, inverse, category, directed in RELATIONSHIP_TYPES
    ])
    op.execute("""
        INSERT INTO relationship_entities (entity_type, person_id, name)
        SELECT 'person', id, COALESCE(full_name, 'Person ' || id::text) FROM people
        ON CONFLICT (person_id) DO NOTHING
    """)
    op.execute("""
        INSERT INTO relationship_entities (entity_type, household_id, name)
        SELECT 'household', id, name FROM households
        ON CONFLICT (household_id) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_index(
        "uq_household_primary_per_person",
        table_name="household_relationships",
    )
    op.drop_index("ix_relationships_to_active", table_name="relationships")
    op.drop_index("ix_relationships_from_active", table_name="relationships")
    op.drop_table("relationships")
    op.drop_table("relationship_entities")
    op.drop_table("relationship_types")
    op.drop_column("household_relationships", "is_primary_household")
