import os

from dotenv import load_dotenv
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)

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
    Column("custodian_id", Integer),
    Column("registration_id", Integer),
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
    Column("due_date", Date),
    Column("completed_at", DateTime(timezone=True)),
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
    Column("uploaded_by", String(255)),
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
if __name__ == "__main__":
    metadata.create_all(engine)
    print("Client360 Version 1 schema initialized successfully.")
