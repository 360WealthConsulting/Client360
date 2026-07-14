"""hot-path foreign-key indexes (batch 2)

Revision ID: o5f36c4d3e2a
Revises: n4e25b3c2f1d

Release 0.9.9 (Platform Consolidation), Phase 4 — Database Optimization.

Batch-2 completes the hot-path foreign-key indexing for the remaining
query-justified scope columns on the portfolio, portal, and workflow read paths.
Each column below was confirmed to be an actual query predicate (call sites in
comments) and to lack a leading-column index. Columns that are pure audit
back-references (created_by/updated_by/reviewer/approver) are intentionally NOT
indexed — they are not query predicates on any hot path.

CREATE INDEX CONCURRENTLY inside an autocommit block; reversible; single head.

Index -> query it supports (representative call sites):
  accounts(person_id)                            portfolio per-client join (portfolio.py:23/38; people.py:67/263)
  accounts(household_id)                          household AUM rollup (portfolio.py:22/24/31)
  microsoft_documents(person_id)                  person document match/sync
  portal_accounts(person_id)                      portal account resolution by person
  portal_document_requests(person_id)             portal open requests (portal/service.py:225)
  portal_message_receipts(portal_account_id)      read-receipt lookups (portal/service.py:162)
  portal_sessions(portal_account_id)              portal session validation (hot per-request)
  workflow_events(workflow_instance_id)           workflow history load (workflow_automation.py:175)
  workflow_instances(person_id)                   portal workflow scope join (portal/service.py:204)
  workflow_instances(household_id)               portal workflow scope join (portal/service.py:204)
"""
from alembic import op

revision = "o5f36c4d3e2a"
down_revision = "n4e25b3c2f1d"
branch_labels = None
depends_on = None


INDEXES = [
    ("ix_accounts_person_id", "accounts", "person_id"),
    ("ix_accounts_household_id", "accounts", "household_id"),
    ("ix_microsoft_documents_person_id", "microsoft_documents", "person_id"),
    ("ix_portal_accounts_person_id", "portal_accounts", "person_id"),
    ("ix_portal_document_requests_person_id", "portal_document_requests", "person_id"),
    ("ix_portal_message_receipts_portal_account_id", "portal_message_receipts", "portal_account_id"),
    ("ix_portal_sessions_portal_account_id", "portal_sessions", "portal_account_id"),
    ("ix_workflow_events_workflow_instance_id", "workflow_events", "workflow_instance_id"),
    ("ix_workflow_instances_person_id", "workflow_instances", "person_id"),
    ("ix_workflow_instances_household_id", "workflow_instances", "household_id"),
]


def upgrade():
    with op.get_context().autocommit_block():
        for name, table, columns in INDEXES:
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} ({columns})"
            )


def downgrade():
    with op.get_context().autocommit_block():
        for name, _table, _columns in reversed(INDEXES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
