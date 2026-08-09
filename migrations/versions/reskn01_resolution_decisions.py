"""folder_resolution_decisions — durable, subject-generic resolution/alias knowledge ledger.

Creates ``folder_resolution_decisions``: the durable REUSE layer recording how an unresolved ingestion
subject (initially a TaxDome folder alias; later acquired advisor books, acquired firms, scanned-paper
batches, CRM records, and other ingestion sources) was resolved to a canonical Client360 outcome. It is a
reuse layer on top of the canonical provenance tables (person_source_links / households /
relationship_entities) — NOT a replacement.

Key properties (see app/database/identity_tables.py for the full rationale):
  * subject-generic identity: (subject_system, subject_type, subject_key) + display_name;
  * full history retained: decisions are append-only; a correction supersedes the prior row
    (active=false, superseded_at, superseded_by -> the new row) instead of overwriting/deleting it;
  * a PARTIAL UNIQUE index enforces exactly ONE active row per (subject_system, subject_type, subject_key);
  * fail-closed CHECK: the decision must agree with the resulting entity — entity-linking decisions carry
    the matching entity type + a non-null id; firm_material carries type 'firm' + null id; reject/defer/
    ambiguous carry neither. Non-positive dispositions can therefore never masquerade as matching knowledge.

This migration ONLY creates the ledger table: no document links, no file movement, no storage_uri /
document_sources change, no canonical/document-link APPLY.

Downgrade drops the table (safe on a non-production database only).

Revision ID: reskn01
Revises: drake01
Create Date: 2026-08-08
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "reskn01"
down_revision = "drake01"
branch_labels = None
depends_on = None

_ENTITY = ("link_person", "create_person", "link_household", "create_household",
           "link_business", "create_business")
_POSITIVE = _ENTITY + ("firm_material",)
_NON_REUSABLE = ("reject", "defer", "ambiguous")
_ALL = _POSITIVE + _NON_REUSABLE
_LIST = ", ".join(f"'{d}'" for d in _ALL)

_DECISION_ENTITY_CHECK = (
    "(decision IN ('link_person', 'create_person') "
    "  AND resulting_entity_type = 'person' AND resulting_entity_id IS NOT NULL) "
    "OR (decision IN ('link_household', 'create_household') "
    "  AND resulting_entity_type = 'household' AND resulting_entity_id IS NOT NULL) "
    "OR (decision IN ('link_business', 'create_business') "
    "  AND resulting_entity_type = 'relationship_entity' AND resulting_entity_id IS NOT NULL) "
    "OR (decision = 'firm_material' "
    "  AND resulting_entity_type = 'firm' AND resulting_entity_id IS NULL) "
    "OR (decision IN ('reject', 'defer', 'ambiguous') "
    "  AND resulting_entity_type IS NULL AND resulting_entity_id IS NULL)"
)


def upgrade() -> None:
    op.create_table(
        "folder_resolution_decisions",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("subject_system", sa.String(100), nullable=False),
        sa.Column("subject_type", sa.String(50), nullable=False, server_default="folder"),
        sa.Column("subject_key", sa.String(500), nullable=False),
        sa.Column("display_name", sa.String(500), nullable=False),
        sa.Column("decision", sa.String(50), nullable=False),
        sa.Column("resulting_entity_type", sa.String(50)),
        sa.Column("resulting_entity_id", sa.BigInteger),
        sa.Column("evidence_snapshot", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("match_reason", sa.Text),
        sa.Column("confidence", sa.Numeric(5, 2)),
        sa.Column("evidence_metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("reviewed_by", sa.String(255)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("exception_id", sa.Integer),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column("superseded_by", sa.BigInteger,
                  sa.ForeignKey("folder_resolution_decisions.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(f"decision IN ({_LIST})", name="ck_frd_decision"),
        sa.CheckConstraint(_DECISION_ENTITY_CHECK, name="ck_frd_decision_entity"),
    )
    # Exactly one ACTIVE resolution per subject triple; superseded history rows are unconstrained.
    op.create_index("uq_frd_active", "folder_resolution_decisions",
                    ["subject_system", "subject_type", "subject_key"],
                    unique=True, postgresql_where=sa.text("active"))
    op.create_index("ix_frd_subject", "folder_resolution_decisions",
                    ["subject_system", "subject_type", "subject_key"])
    op.create_index("ix_frd_exception", "folder_resolution_decisions", ["exception_id"])


def downgrade() -> None:
    op.drop_index("ix_frd_exception", table_name="folder_resolution_decisions")
    op.drop_index("ix_frd_subject", table_name="folder_resolution_decisions")
    op.drop_index("uq_frd_active", table_name="folder_resolution_decisions")
    op.drop_table("folder_resolution_decisions")
