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
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint(
        "custodian",
        "account_number",
        name="uq_account_custodian_number",
    ),
)


if __name__ == "__main__":
    metadata.create_all(engine)
    print("Client360 Version 1 schema initialized successfully.")
