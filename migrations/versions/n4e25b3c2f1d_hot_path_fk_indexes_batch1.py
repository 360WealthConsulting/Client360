"""hot-path foreign-key indexes (batch 1)

Revision ID: n4e25b3c2f1d
Revises: m3d14a2f1e0c

Release 0.9.9 (Platform Consolidation), Phase 4 — Database Optimization.

PostgreSQL does not auto-index foreign-key columns, so the hot per-request
client/household/portal read paths were seq-scanning these tables (RC9 H20;
architecture Sec 5/Sec 23). This migration adds the batch-1 hot-path indexes.

Each index is built with CREATE INDEX CONCURRENTLY inside an autocommit block so
production builds do not hold a write lock on these tables; DROP likewise uses
CONCURRENTLY. IF NOT EXISTS / IF EXISTS make the migration re-runnable. No data
change; additive and reversible; single head preserved.

Index -> query it supports (representative call sites):
  people(household_id)                       household member expansion (portal/service.py:102)
  tasks(person_id)                           per-client task lists / client_summary
  activities(person_id)                      per-client activity feed
  documents(person_id)                       per-client documents (portal/service.py:228; work_management.py:232)
  timeline_events(person_id)                 per-client timeline (portal/service.py:229; work_management.py:233)
  timeline_events(household_id)              household timeline (portal/service.py:229)
  household_relationships(person_id)         person -> households reverse lookup
  portal_notifications(portal_account_id)    portal notifications (portal/service.py:226)
  portal_threads(household_id)               portal threads scope (portal/service.py:128/227)
  portal_threads(person_id)                  portal threads scope (portal/service.py:128/227)
  tax_engagements(person_id)                 per-client engagements
  tax_engagements(household_id)              household engagements
  audit_events(actor_user_id)                actor activity audit queries
  audit_events(entity_type, entity_id)       per-record audit trail lookup
"""
from alembic import op

revision = "n4e25b3c2f1d"
down_revision = "m3d14a2f1e0c"
branch_labels = None
depends_on = None


# (index_name, table, columns-SQL)
INDEXES = [
    ("ix_people_household_id", "people", "household_id"),
    ("ix_tasks_person_id", "tasks", "person_id"),
    ("ix_activities_person_id", "activities", "person_id"),
    ("ix_documents_person_id", "documents", "person_id"),
    ("ix_timeline_events_person_id", "timeline_events", "person_id"),
    ("ix_timeline_events_household_id", "timeline_events", "household_id"),
    ("ix_household_relationships_person_id", "household_relationships", "person_id"),
    ("ix_portal_notifications_portal_account_id", "portal_notifications", "portal_account_id"),
    ("ix_portal_threads_household_id", "portal_threads", "household_id"),
    ("ix_portal_threads_person_id", "portal_threads", "person_id"),
    ("ix_tax_engagements_person_id", "tax_engagements", "person_id"),
    ("ix_tax_engagements_household_id", "tax_engagements", "household_id"),
    ("ix_audit_events_actor_user_id", "audit_events", "actor_user_id"),
    ("ix_audit_events_entity", "audit_events", "entity_type, entity_id"),
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
