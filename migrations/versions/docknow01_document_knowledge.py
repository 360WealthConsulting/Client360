"""document_classifications + document_facts — the Knowledge layer over canonical documents (ADR-072)

Phase 6A enriches the EXISTING canonical ``documents`` row with intelligence; it does not create a second
document system, an AI application, or Intelligence-specific screens. Two additive tables:

- ``document_classifications`` — one current classification per canonical document (broad doc type:
  1040/W-2/1099/K-1/IRS notice/passport/insurance policy/trust/…), with confidence + classifier version.
  Distinct from the tax-only ``tax_document_classifications`` (that stays as-is; this is the general layer
  the Knowledge pipeline uses).
- ``document_facts`` — versioned Knowledge Objects (structured extracted facts) stored SEPARATELY from
  the OCR text (``document_ocr``). Each fact records its source document, extraction engine + version,
  confidence, and extraction date; a re-extraction supersedes the prior value (``is_current`` flips,
  ``version`` increments) so history is retained. Full SSNs are never stored (only ssn_last4).

No ADR change; ownership is derived from the canonical document (ADR-073).

Revision ID: docknow01
Revises: dococr01
Create Date: 2026-08-02
"""
import sqlalchemy as sa
from alembic import op

revision = "docknow01"
down_revision = "dococr01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_classifications",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer,
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("doc_type", sa.Text, nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False, server_default="0"),
        sa.Column("classifier_version", sa.Text, nullable=False),
        sa.Column("classified_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1",
                           name="ck_document_classification_confidence"),
        sa.UniqueConstraint("document_id", name="uq_document_classification_document"),
    )
    op.create_index("ix_document_classifications_type", "document_classifications", ["doc_type"])

    op.create_table(
        "document_facts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer,
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fact_type", sa.Text, nullable=False),
        sa.Column("fact_value", sa.Text),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False, server_default="0"),
        sa.Column("extraction_engine", sa.Text, nullable=False),
        sa.Column("extractor_version", sa.Text),
        sa.Column("extracted_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("is_current", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_document_fact_confidence"),
    )
    op.create_index("ix_document_facts_document", "document_facts", ["document_id"])
    op.create_index("ix_document_facts_type", "document_facts", ["fact_type"])
    # One current row per distinct (document, fact_type, value) — allows multi-value fact types
    # (several dates/amounts) while a re-extraction of the same value supersedes its prior version
    # (is_current flips, version increments). Prior versions are retained (is_current=false).
    op.create_index("uq_document_fact_current", "document_facts",
                    ["document_id", "fact_type", "fact_value"],
                    unique=True, postgresql_where=sa.text("is_current"))


def downgrade() -> None:
    op.drop_index("uq_document_fact_current", table_name="document_facts")
    op.drop_index("ix_document_facts_type", table_name="document_facts")
    op.drop_index("ix_document_facts_document", table_name="document_facts")
    op.drop_table("document_facts")
    op.drop_index("ix_document_classifications_type", table_name="document_classifications")
    op.drop_table("document_classifications")
