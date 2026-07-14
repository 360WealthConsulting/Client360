"""add tax document intelligence and missing-information linkage

Revision ID: k1b92e0d9c8a
Revises: j0a81f9c8d7e

Sprint 5.4 — Tax Document Intelligence & Missing Information. Adds document
link/classification/evidence/review tables for the deterministic matching engine
(see docs/SPRINT_5_4_TAX_DOCUMENT_INTELLIGENCE.md), the tax.document.review
capability, review work queues, and the FK index RC9 flagged on
tax_missing_items. Deactivates legacy free-text Microsoft matching rules so the
removed substring matcher can never be re-evaluated (H13); the new engine only
honours structured exact rule types.
"""
from alembic import op
import sqlalchemy as sa
import json

revision = "k1b92e0d9c8a"
down_revision = "j0a81f9c8d7e"
branch_labels = None
depends_on = None

NEW_TABLES = ("tax_document_links", "tax_document_classifications",
              "tax_document_match_evidence", "tax_document_review_events")
IMMUTABLE_TABLES = ("tax_document_match_evidence", "tax_document_review_events")


def upgrade():
    op.create_table("tax_document_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tax_engagement_return_id", sa.Integer(), sa.ForeignKey("tax_engagement_returns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tax_checklist_item_id", sa.Integer(), sa.ForeignKey("tax_checklist_items.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False, server_default="0"),
        sa.Column("match_source", sa.String(20), nullable=False),
        sa.Column("category", sa.String(60)),
        sa.Column("matched_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('proposed','accepted','rejected','superseded')", name="ck_tax_document_link_status"),
        sa.CheckConstraint("match_source IN ('portal_request','drive_rule','email_exact','hash','manual','ai_port')", name="ck_tax_document_link_source"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_tax_document_link_confidence"),
    )
    op.create_index("ix_tax_document_links_document", "tax_document_links", ["document_id"])
    op.create_index("ix_tax_document_links_return", "tax_document_links", ["tax_engagement_return_id"])
    op.create_index("ix_tax_document_links_checklist", "tax_document_links", ["tax_checklist_item_id"])
    op.create_index("ix_tax_document_links_status", "tax_document_links", ["status", "confidence"])
    # At most one accepted link per (document, return).
    op.create_index("uq_tax_document_link_accepted", "tax_document_links",
        ["document_id", "tax_engagement_return_id"], unique=True,
        postgresql_where=sa.text("status = 'accepted'"))

    op.create_table("tax_document_classifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(60), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False, server_default="0"),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("reviewer_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("provenance", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("source IN ('deterministic','rule','manual','ai_port')", name="ck_tax_document_classification_source"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_tax_document_classification_confidence"),
    )
    op.create_index("ix_tax_document_classifications_document", "tax_document_classifications", ["document_id"])

    op.create_table("tax_document_match_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tax_document_link_id", sa.Integer(), sa.ForeignKey("tax_document_links.id", ondelete="CASCADE"), nullable=False),
        sa.Column("signal_type", sa.String(30), nullable=False),
        sa.Column("value_hash", sa.String(64)),
        sa.Column("weight", sa.Numeric(4, 3), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("signal_type IN ('portal_request','drive_rule','email_exact','hash','manual','hint')", name="ck_tax_document_evidence_signal"),
    )
    op.create_index("ix_tax_document_evidence_link", "tax_document_match_evidence", ["tax_document_link_id"])

    op.create_table("tax_document_review_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tax_document_link_id", sa.Integer(), sa.ForeignKey("tax_document_links.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reason", sa.Text()),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("action IN ('accept','reject','reassign','classify','duplicate','revert')", name="ck_tax_document_review_action"),
    )
    op.create_index("ix_tax_document_review_link", "tax_document_review_events", ["tax_document_link_id"])

    # RC9 H20: FK index on the missing-item table's return column.
    op.create_index("ix_tax_missing_items_return", "tax_missing_items", ["tax_engagement_return_id"])

    bind = op.get_bind()

    # tax.document.review capability, granted to administrator (the only role that
    # holds any tax.* capability today); composable into future reviewer roles.
    bind.execute(sa.text("INSERT INTO capabilities(code,description,sensitive) VALUES ('tax.document.review','Review and match authorized tax documents',true)"))
    bind.execute(sa.text("INSERT INTO role_capabilities(role_id,capability_id) SELECT r.id,c.id FROM roles r CROSS JOIN capabilities c WHERE r.code='administrator' AND c.code='tax.document.review'"))

    for code, name, criteria in (
        ("tax_doc_unmatched", "Tax — Unmatched Documents", {"work_type": "tax_document", "status": "unmatched"}),
        ("tax_doc_review", "Tax — Document Match Review", {"work_type": "tax_document", "status": "match_review"}),
        ("tax_doc_duplicate", "Tax — Duplicate Documents", {"work_type": "tax_document", "status": "duplicate_review"}),
        ("tax_doc_classification", "Tax — Classification Review", {"work_type": "tax_document", "status": "classification_review"}),
    ):
        bind.execute(sa.text("INSERT INTO work_queues(code,name,description,criteria,required_capability) VALUES (:c,:n,:n,CAST(:x AS json),'tax.read')"),
                     {"c": code, "n": name, "x": json.dumps(criteria)})

    # Append-only ledger protection for evidence and review events (H13/audit).
    op.execute("CREATE FUNCTION prevent_tax_document_event_mutation() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'tax document events are append-only'; END; $$ LANGUAGE plpgsql")
    for table in IMMUTABLE_TABLES:
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION prevent_tax_document_event_mutation()")

    # Legacy Microsoft matching rules used free-text substring patterns (H13).
    # Deactivate them so the removed substring matcher can never run, and bound
    # rule_type to the structured exact types plus retained legacy labels (kept
    # only so existing inactive rows remain valid; the engine ignores them).
    op.execute("UPDATE microsoft_document_matching_rules SET active = false")
    op.create_check_constraint("ck_microsoft_matching_rule_type", "microsoft_document_matching_rules",
        "rule_type IN ('drive_id','folder_item_id','email_exact','filename','folder','email','metadata')")


def downgrade():
    op.drop_constraint("ck_microsoft_matching_rule_type", "microsoft_document_matching_rules", type_="check")
    for table in IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS prevent_tax_document_event_mutation()")
    op.execute("DELETE FROM work_queues WHERE code LIKE 'tax_doc_%'")
    op.execute("DELETE FROM role_capabilities WHERE capability_id IN (SELECT id FROM capabilities WHERE code = 'tax.document.review')")
    op.execute("DELETE FROM capabilities WHERE code = 'tax.document.review'")
    op.drop_index("ix_tax_missing_items_return", table_name="tax_missing_items")
    for name in reversed(NEW_TABLES):
        op.drop_table(name)
