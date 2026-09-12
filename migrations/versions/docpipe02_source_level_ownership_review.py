"""One ownership review per SOURCE identity, not per document.

THE PROBLEM THIS FIXES
----------------------
``docpipe01``'s ``document_pipeline_ownership_reviews`` is UNIQUE(document_id): one row per document.
That is right for a document-specific question and wrong for the question the authoritative lanes
actually raise, which is about a CLIENT. On the production corpus, 474 ownership conflicts come from
18 TaxDome folders — one of them accounts for 121 documents. Document-level rows would put 474 items
in front of a reviewer for 18 decisions, and 121 of them would be the same decision restated.

A reviewer who is shown the same question 121 times stops reading it. So conflicts raised by a stable
source identity aggregate onto ONE review, and the documents hang off it.

THE KEYS ARE STABLE IDENTITIES, NOT DISPLAY NAMES
--------------------------------------------------
``(subject_system, subject_type, subject_key)`` — deliberately the same triple
``folder_resolution_decisions`` uses, so a review and the durable mapping that resolves it are keyed
the same way and can be joined without a translation layer. ``subject_key`` is a normalised identity
(a Drake client id, a TaxDome account id or normalised folder key); ``display_name`` is stored ONCE
per review for a human to read, rather than repeated on every affected document row.

WHAT IS NOT HERE
----------------
No count column. A denormalised count drifts the moment anything touches the membership table by
another path, and the count is a cheap aggregate over an indexed foreign key. The service computes it.

``docpipe01`` is NOT modified. Its per-document review table stays exactly as it is: SharePoint
evidence has no stable client key to aggregate on in the general case, so it keeps document-level
review, and that table keeps serving it.

Revision ID: docpipe02
Revises: docpub01
Create Date: 2026-09-12
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "docpipe02"
down_revision = "docpub01"
branch_labels = None
depends_on = None

_STATUSES = ("open", "resolved", "dismissed")

#: Entity vocabulary, matching ``folder_resolution_decisions.resulting_entity_type`` so a review's
#: recorded outcome and the durable mapping it produces never disagree about what a "business" is.
_ENTITY_TYPES = ("person", "household", "relationship_entity")


def _in(column, values):
    return f"{column} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "document_pipeline_source_reviews",
        sa.Column("id", sa.BigInteger, primary_key=True),
        # The stable source identity. Same triple as folder_resolution_decisions.
        sa.Column("subject_system", sa.String(100), nullable=False),
        sa.Column("subject_type", sa.String(50), nullable=False),
        sa.Column("subject_key", sa.String(500), nullable=False),
        # Stored once per review, for a human. Never repeated per document.
        sa.Column("display_name", sa.String(500)),
        sa.Column("lane", sa.Text, nullable=False),
        sa.Column("reason_code", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("evidence", JSONB, nullable=False, server_default="[]"),
        sa.Column("candidates", JSONB, nullable=False, server_default="[]"),
        # What a reviewer decided. Recorded ONCE for the whole source identity.
        sa.Column("resolution_entity_type", sa.String(50)),
        sa.Column("resolution_entity_id", sa.BigInteger),
        sa.Column("resolution_note", sa.Text),
        sa.Column("resolved_by_user_id", sa.Integer),
        sa.Column("opened_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True)),
        sa.CheckConstraint(_in("status", _STATUSES), name="ck_document_pipeline_source_review_status"),
        sa.CheckConstraint(
            "(resolution_entity_type IS NULL AND resolution_entity_id IS NULL) OR "
            f"({_in('resolution_entity_type', _ENTITY_TYPES)} AND resolution_entity_id IS NOT NULL)",
            name="ck_document_pipeline_source_review_entity"),
    )
    # Exactly ONE OPEN review per source identity. Resolved history is unconstrained, so the same
    # folder can be reviewed again later without deleting what was decided before.
    op.create_index("uq_document_pipeline_source_review_open",
                    "document_pipeline_source_reviews",
                    ["subject_system", "subject_type", "subject_key"],
                    unique=True, postgresql_where=sa.text("status = 'open'"))
    op.create_index("ix_document_pipeline_source_reviews_status",
                    "document_pipeline_source_reviews", ["status", "lane"])

    op.create_table(
        "document_pipeline_source_review_documents",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("review_id", sa.BigInteger,
                  sa.ForeignKey("document_pipeline_source_reviews.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("document_id", sa.Integer,
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("added_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        # A document joins a review once. A later document joins the SAME review rather than opening
        # another, which is the whole point of the table.
        sa.UniqueConstraint("review_id", "document_id",
                            name="uq_document_pipeline_source_review_document"),
    )
    op.create_index("ix_document_pipeline_source_review_documents_document",
                    "document_pipeline_source_review_documents", ["document_id"])

    # Deliberately NO free-text column on the membership table: it is a join row, and anything a
    # reviewer would write belongs on the review itself, once.


def downgrade() -> None:
    op.drop_index("ix_document_pipeline_source_review_documents_document",
                  table_name="document_pipeline_source_review_documents")
    op.drop_table("document_pipeline_source_review_documents")
    op.drop_index("ix_document_pipeline_source_reviews_status",
                  table_name="document_pipeline_source_reviews")
    op.drop_index("uq_document_pipeline_source_review_open",
                  table_name="document_pipeline_source_reviews")
    op.drop_table("document_pipeline_source_reviews")
