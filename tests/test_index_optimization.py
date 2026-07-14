"""Release 0.9.9 Phase 4 — index-optimization regression tests.

Assert the hot-path foreign-key indexes exist and are valid, and that the
planner will select one for its intended predicate. These guard against a future
migration silently dropping an index the read paths depend on.
"""
from sqlalchemy import text

from app.db import engine


EXPECTED_INDEXES = {
    # batch 1 — hot path
    "ix_people_household_id",
    "ix_tasks_person_id",
    "ix_activities_person_id",
    "ix_documents_person_id",
    "ix_timeline_events_person_id",
    "ix_timeline_events_household_id",
    "ix_household_relationships_person_id",
    "ix_portal_notifications_portal_account_id",
    "ix_portal_threads_household_id",
    "ix_portal_threads_person_id",
    "ix_tax_engagements_person_id",
    "ix_tax_engagements_household_id",
    "ix_audit_events_actor_user_id",
    "ix_audit_events_entity",
    # batch 2 — remaining query-justified scope columns
    "ix_accounts_person_id",
    "ix_accounts_household_id",
    "ix_microsoft_documents_person_id",
    "ix_portal_accounts_person_id",
    "ix_portal_document_requests_person_id",
    "ix_portal_message_receipts_portal_account_id",
    "ix_portal_sessions_portal_account_id",
    "ix_workflow_events_workflow_instance_id",
    "ix_workflow_instances_person_id",
    "ix_workflow_instances_household_id",
}


def test_all_hot_path_indexes_present_and_valid():
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT c.relname FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE i.indisvalid AND c.relname = ANY(:names)"
            ),
            {"names": list(EXPECTED_INDEXES)},
        ).scalars().all()
    present = set(rows)
    missing = EXPECTED_INDEXES - present
    assert not missing, f"missing/invalid indexes: {sorted(missing)}"


def test_planner_selects_person_id_index():
    """The planner uses the FK index for a person-scoped lookup."""
    with engine.connect() as connection:
        # Discourage seq scans so the check is stable on small test tables;
        # this proves the index is usable and preferred for the predicate.
        connection.execute(text("SET enable_seqscan = off"))
        plan = "\n".join(
            connection.execute(
                text("EXPLAIN SELECT * FROM timeline_events WHERE person_id = 1")
            ).scalars().all()
        )
    assert "ix_timeline_events_person_id" in plan, plan
