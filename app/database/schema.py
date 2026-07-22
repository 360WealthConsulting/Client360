import os

from dotenv import load_dotenv
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)

from app.database.advisor_work_tables import define_advisor_work_tables
from app.database.analytics_tables import define_analytics_tables
from app.database.annual_review_tables import define_annual_review_tables
from app.database.automation_tables import define_automation_tables
from app.database.business_planning_tables import define_business_planning_tables
from app.database.campaign_referral_tables import define_campaign_referral_tables
from app.database.communication_tables import define_communication_tables
from app.database.compliance_tables import define_compliance_tables
from app.database.document_platform_tables import define_document_platform_tables
from app.database.identity_tables import define_identity_tables
from app.database.operations_tables import define_operations_tables
from app.database.opportunity_tables import define_opportunity_tables
from app.database.outbox_tables import define_outbox_tables
from app.database.reporting_tables import define_reporting_tables
from app.database.scheduling_tables import define_scheduling_tables
from app.database.work_tables import define_work_tables

load_dotenv("app/.env")

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing from app/.env")

engine = create_engine(DATABASE_URL)
metadata = MetaData()

from app.database.portfolio_tables import define_portfolio_tables

households = Table(
    "households",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(255), nullable=False),
    Column("address_line_1", String(255)),
    Column("address_line_2", String(255)),
    Column("city", String(100)),
    Column("state", String(50)),
    Column("postal_code", String(20)),
    Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("updated_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column(
        "updated_at",
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    ),
)


people = Table(
    "people",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("household_id", Integer, ForeignKey("households.id")),
    Column("first_name", String(100)),
    Column("middle_name", String(100)),
    Column("last_name", String(100)),
    Column("full_name", String(255)),
    Column("preferred_name", String(100)),
    Column("birth_date", Date),
    Column("primary_email", String(255)),
    Column("normalized_email", String(255), index=True),
    Column("primary_phone", String(50)),
    Column("normalized_phone", String(30), index=True),
    Column("address_line_1", String(255)),
    Column("address_line_2", String(255)),
    Column("city", String(100)),
    Column("state", String(50)),
    Column("postal_code", String(20)),
    Column("contact_type", String(100)),
    Column("active", Boolean, default=True),
    Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("updated_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column(
        "updated_at",
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    ),
)


source_contacts = Table(
    "source_contacts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_system", String(100), nullable=False),
    Column("source_file", String(500), nullable=False),
    Column("source_record_id", String(255)),
    Column("source_hash", String(64), nullable=False),
    Column("first_name", String(100)),
    Column("middle_name", String(100)),
    Column("last_name", String(100)),
    Column("full_name", String(255)),
    Column("email", String(255)),
    Column("normalized_email", String(255), index=True),
    Column("phone", String(50)),
    Column("normalized_phone", String(30), index=True),
    Column("address_line_1", String(255)),
    Column("address_line_2", String(255)),
    Column("city", String(100)),
    Column("state", String(50)),
    Column("postal_code", String(20)),
    Column("connected_at", DateTime(timezone=True)),
    Column("territory", String(255)),
    Column("raw_data", JSON, nullable=False),
    Column("imported_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint(
        "source_system",
        "source_hash",
        name="uq_source_contacts_system_hash",
    ),
)


person_source_links = Table(
    "person_source_links",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("person_id", Integer, ForeignKey("people.id"), nullable=False),
    Column(
        "source_contact_id",
        Integer,
        ForeignKey("source_contacts.id"),
        nullable=False,
    ),
    Column("match_method", String(100)),
    Column("match_score", Numeric(5, 2)),
    Column("confirmed", Boolean, default=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint(
        "person_id",
        "source_contact_id",
        name="uq_person_source_link",
    ),
)


accounts = Table(
    "accounts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("person_id", Integer, ForeignKey("people.id")),
    Column("household_id", Integer, ForeignKey("households.id")),
    Column("custodian", String(100), nullable=False),
    Column("account_number", String(100)),
    Column("account_name", String(255)),
    Column("registration_type", String(255)),
    Column("status", String(100)),
    Column("total_value", Numeric(18, 2)),
    Column("cash_value", Numeric(18, 2)),
    Column("open_date", Date),
    Column("closed_date", Date),
    Column("source_file", String(500)),
    Column("custodian_id", Integer, ForeignKey("custodians.id")),
    Column("registration_id", Integer, ForeignKey("account_registrations.id")),
    Column("last_imported_at", DateTime(timezone=True)),
    Column("last_review_date", Date),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint(
        "custodian",
        "account_number",
        name="uq_account_custodian_number",
    ),
)


import_jobs = Table(
    "import_jobs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_system", String(100), nullable=False),
    Column("source_file", String(500)),
    Column("file_hash", String(64), index=True),
    Column("status", String(50), nullable=False, server_default="started"),
    Column("started_at", DateTime(timezone=True), server_default=func.now()),
    Column("completed_at", DateTime(timezone=True)),
    Column("rows_read", Integer, nullable=False, server_default="0"),
    Column("rows_inserted", Integer, nullable=False, server_default="0"),
    Column("rows_updated", Integer, nullable=False, server_default="0"),
    Column("rows_skipped", Integer, nullable=False, server_default="0"),
    Column("error_message", Text),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)


match_queue = Table(
    "match_queue",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "source_contact_id",
        Integer,
        ForeignKey("source_contacts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "candidate_person_id",
        Integer,
        ForeignKey("people.id", ondelete="CASCADE"),
    ),
    Column("match_score", Numeric(5, 2), nullable=False),
    Column("match_method", String(100)),
    Column("status", String(50), nullable=False, server_default="pending"),
    Column("reviewed_at", DateTime(timezone=True)),
    Column("reviewed_by", String(255)),
    Column("decision_notes", Text),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint(
        "source_contact_id",
        "candidate_person_id",
        name="uq_match_queue_candidate",
    ),
)

tasks = Table(
    "tasks",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "person_id",
        Integer,
        ForeignKey("people.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("title", String(255), nullable=False),
    Column("description", Text),
    Column("status", String(50), nullable=False, server_default="open"),
    Column("priority", String(50), nullable=False, server_default="normal"),
    Column("assigned_to", String(255)),
    Column("household_id", Integer, ForeignKey("households.id", ondelete="CASCADE")),
    Column("team_id", Integer, ForeignKey("teams.id", ondelete="SET NULL")),
    Column("workflow_name", String(255)),
    Column("work_type", String(100), nullable=False, server_default="general"),
    Column("waiting_on", String(50)),
    Column("due_date", Date),
    Column("sla_due_at", DateTime(timezone=True)),
    Column("estimated_minutes", Integer, nullable=False, server_default="30"),
    Column("completed_at", DateTime(timezone=True)),
    Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("updated_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column(
        "created_at",
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    ),
    # Per-submission idempotency token (unique when set) so a resubmitted create-task
    # form cannot produce a duplicate task. NULLs stay distinct in Postgres.
    Column("idempotency_key", String(64)),
)



activities = Table(
    "activities",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "person_id",
        Integer,
        ForeignKey("people.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("activity_type", String(100), nullable=False),
    Column("title", String(255), nullable=False),
    Column("details", Text),
    Column(
        "occurred_at",
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
    Column("created_by", String(255)),
    Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("updated_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column(
        "created_at",
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
)



household_relationships = Table(
    "household_relationships",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "household_id",
        Integer,
        ForeignKey("households.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "person_id",
        Integer,
        ForeignKey("people.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("relationship_type", String(100), nullable=False),
    Column("is_primary", Boolean, nullable=False, server_default="false"),
    Column(
        "is_primary_household",
        Boolean,
        nullable=False,
        server_default="false",
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
    UniqueConstraint(
        "household_id",
        "person_id",
        name="uq_household_relationship_person",
    ),
)




documents = Table(
    "documents",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "person_id",
        Integer,
        ForeignKey("people.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("original_name", String(500), nullable=False),
    Column("stored_name", String(500), nullable=False, unique=True),
    Column("storage_path", String(1000), nullable=False),
    Column("content_type", String(255)),
    Column("size_bytes", Integer, nullable=False),
    Column("sha256", String(64), nullable=False, index=True),
    Column("category", String(100)),
    Column("description", Text),
    Column("review_status", String(50), nullable=False, server_default="not_required"),
    Column("review_due_at", DateTime(timezone=True)),
    Column("reviewer_team_id", Integer, ForeignKey("teams.id", ondelete="SET NULL")),
    Column("uploaded_by", String(255)),
    Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("updated_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("archived", Boolean, nullable=False, server_default="false"),
    Column(
        "created_at",
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    ),
)


microsoft_drives = Table(
    "microsoft_drives",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("microsoft_drive_id", String(500), nullable=False, unique=True),
    Column("name", String(500)),
    Column("drive_type", String(100)),
    Column("source_type", String(50), nullable=False),
    Column("site_id", String(500)),
    Column("web_url", Text),
    Column("delta_link", Text),
    Column("last_synced_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


microsoft_documents = Table(
    "microsoft_documents",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("microsoft_drive_id", String(500), nullable=False),
    Column("microsoft_item_id", String(500), nullable=False),
    Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
    Column("name", String(500), nullable=False),
    Column("mime_type", String(255)),
    Column("size_bytes", Integer, nullable=False, server_default="0"),
    Column("web_url", Text),
    Column("parent_path", Text),
    Column("created_at_microsoft", DateTime(timezone=True)),
    Column("modified_at_microsoft", DateTime(timezone=True)),
    Column("created_by_email", String(320)),
    Column("modified_by_email", String(320)),
    Column("match_method", String(100)),
    Column("status", String(50), nullable=False, server_default="pending"),
    Column("deleted", Boolean, nullable=False, server_default="false"),
    Column("raw_metadata", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint(
        "microsoft_drive_id",
        "microsoft_item_id",
        name="uq_microsoft_document_drive_item",
    ),
)


microsoft_document_matching_rules = Table(
    "microsoft_document_matching_rules",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("person_id", Integer, ForeignKey("people.id", ondelete="CASCADE"), nullable=False),
    Column("rule_type", String(50), nullable=False),
    Column("pattern", String(500), nullable=False),
    Column("priority", Integer, nullable=False, server_default="100"),
    Column("active", Boolean, nullable=False, server_default="true"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint(
        "person_id",
        "rule_type",
        "pattern",
        name="uq_microsoft_document_matching_rule",
    ),
)


relationship_types = Table(
    "relationship_types",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("code", String(100), nullable=False, unique=True),
    Column("name", String(255), nullable=False),
    Column("inverse_name", String(255)),
    Column("category", String(100), nullable=False),
    Column("directed", Boolean, nullable=False, server_default="true"),
    Column("active", Boolean, nullable=False, server_default="true"),
)


relationship_entities = Table(
    "relationship_entities",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("entity_type", String(50), nullable=False),
    Column("person_id", Integer, ForeignKey("people.id", ondelete="CASCADE"), unique=True),
    Column("household_id", Integer, ForeignKey("households.id", ondelete="CASCADE"), unique=True),
    Column("name", String(500), nullable=False),
    Column("details", JSON, nullable=False, server_default="{}"),
    Column("active", Boolean, nullable=False, server_default="true"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


relationships = Table(
    "relationships",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("from_entity_id", Integer, ForeignKey("relationship_entities.id", ondelete="CASCADE"), nullable=False),
    Column("to_entity_id", Integer, ForeignKey("relationship_entities.id", ondelete="CASCADE"), nullable=False),
    Column("relationship_type_id", Integer, ForeignKey("relationship_types.id"), nullable=False),
    Column("effective_date", Date),
    Column("inactive_date", Date),
    Column("notes", Text),
    Column("confidence_level", Numeric(5, 2), nullable=False, server_default="100"),
    Column("source", String(50), nullable=False, server_default="manual"),
    Column("created_by", String(255)),
    Column("active", Boolean, nullable=False, server_default="true"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint(
        "from_entity_id",
        "to_entity_id",
        "relationship_type_id",
        name="uq_relationship_edge",
    ),
)


microsoft_unmatched_messages = Table(
    "microsoft_unmatched_messages",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("microsoft_message_id", String(500), nullable=False),
    Column("sender_name", String(255)),
    Column("sender_address", String(320), nullable=False),
    Column("subject", String(500)),
    Column("body_preview", Text),
    Column("received_at", DateTime(timezone=True)),
    Column("web_link", Text),
    Column("has_attachments", Boolean, nullable=False, server_default="false"),
    Column("status", String(50), nullable=False, server_default="pending"),
    Column("matched_person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint(
        "microsoft_message_id",
        name="uq_microsoft_unmatched_message_id",
    ),
)


microsoft_unmatched_calendar_attendees = Table(
    "microsoft_unmatched_calendar_attendees",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("microsoft_event_id", String(500), nullable=False),
    Column("attendee_email", String(320), nullable=False),
    Column("attendee_name", String(255)),
    Column("attendee_role", String(50)),
    Column("response_status", String(50)),
    Column("subject", String(500)),
    Column("starts_at", DateTime(timezone=True), nullable=False),
    Column("ends_at", DateTime(timezone=True)),
    Column("location", String(500)),
    Column("online_meeting_link", Text),
    Column("web_link", Text),
    Column("event_metadata", JSON, nullable=False),
    Column("status", String(50), nullable=False, server_default="pending"),
    Column(
        "matched_person_id",
        Integer,
        ForeignKey("people.id", ondelete="SET NULL"),
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    ),
    UniqueConstraint(
        "microsoft_event_id",
        "attendee_email",
        name="uq_microsoft_calendar_event_attendee",
    ),
)
Index(
    "ix_microsoft_calendar_review_status_start",
    microsoft_unmatched_calendar_attendees.c.status,
    microsoft_unmatched_calendar_attendees.c.starts_at,
)



timeline_events = Table(
    "timeline_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "person_id",
        Integer,
        ForeignKey("people.id", ondelete="CASCADE"),
    ),
    Column(
        "household_id",
        Integer,
        ForeignKey("households.id", ondelete="CASCADE"),
    ),
    Column("source", String(100), nullable=False),
    Column("event_type", String(100), nullable=False),
    Column("title", String(255), nullable=False),
    Column("summary", Text),
    Column(
        "event_time",
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
    Column("external_id", String(500)),
    Column("event_metadata", JSON),
    Column(
        "created_at",
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    ),
    UniqueConstraint(
        "source",
        "external_id",
        name="uq_timeline_source_external_id",
    ),
)



match_review_decisions = Table(
    "match_review_decisions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("group_key", String(64), nullable=False, unique=True),
    Column("record_ids", JSON, nullable=False),
    Column("decision", String(50), nullable=False),
    Column("reviewed_by", String(255)),
    Column("decision_notes", Text),
    Column(
        "reviewed_at",
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    ),
)
microsoft_accounts = Table(
    "microsoft_accounts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("person_id", Integer, ForeignKey("people.id")),
    Column("tenant_id", String(255), nullable=False),
    Column("user_id", String(255), nullable=False),
    Column("email", String(255), nullable=False),
    Column("display_name", String(255)),
    Column("access_token", Text),
    Column("refresh_token", Text),
    Column("expires_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column(
        "updated_at",
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    ),
    UniqueConstraint(
        "tenant_id",
        "user_id",
        name="uq_microsoft_account",
    ),
)
portfolio_tables = define_portfolio_tables(metadata)
identity_tables = define_identity_tables(metadata)
work_tables = define_work_tables(metadata)
outbox_tables = define_outbox_tables(metadata)
compliance_tables = define_compliance_tables(metadata)
advisor_work_tables = define_advisor_work_tables(metadata)
annual_review_tables = define_annual_review_tables(metadata)
business_planning_tables = define_business_planning_tables(metadata)
opportunity_tables = define_opportunity_tables(metadata)
campaign_referral_tables = define_campaign_referral_tables(metadata)
analytics_tables = define_analytics_tables(metadata)
document_platform_tables = define_document_platform_tables(metadata)
communication_tables = define_communication_tables(metadata)
scheduling_tables = define_scheduling_tables(metadata)
operations_tables = define_operations_tables(metadata)
reporting_tables = define_reporting_tables(metadata)
automation_tables = define_automation_tables(metadata)
if __name__ == "__main__":
    metadata.create_all(engine)
    print("Client360 Version 1 schema initialized successfully.")
