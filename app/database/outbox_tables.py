"""Transactional outbox table definitions (E1.6 / Backlog F1.3).

Generic, additive infrastructure for reliable event publication. These tables are
distinct from and complementary to the existing domain event/automation tables
(``workflow_events``, ``automation_triggers``/``automation_actions``): the outbox
is a low-level delivery primitive (write-in-transaction, poll, retry, dead-letter),
not a domain log or a rules engine.

Composed onto the shared ``metadata`` in app/database/schema.py, alongside the
identity/work/portfolio table modules.
"""
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)


def define_outbox_tables(metadata: MetaData) -> dict[str, Table]:
    outbox_events = Table(
        "outbox_events", metadata,
        Column("id", BigInteger, primary_key=True, autoincrement=True),
        # Idempotency key + stable identity across retries.
        Column("event_id", String(36), nullable=False),
        Column("name", String(200), nullable=False),
        Column("payload", JSON, nullable=False, server_default="{}"),
        # pending -> dispatched | dead
        Column("status", String(20), nullable=False, server_default="pending"),
        Column("attempts", Integer, nullable=False, server_default="0"),
        # Not eligible for dispatch until now >= available_at (backoff).
        Column("available_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("last_error", Text),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("dispatched_at", DateTime(timezone=True)),
        UniqueConstraint("event_id", name="uq_outbox_events_event_id"),
        Index("ix_outbox_events_status_available", "status", "available_at"),
    )
    outbox_dead_letters = Table(
        "outbox_dead_letters", metadata,
        Column("id", BigInteger, primary_key=True, autoincrement=True),
        Column("event_id", String(36), nullable=False),
        Column("name", String(200), nullable=False),
        Column("payload", JSON, nullable=False, server_default="{}"),
        Column("attempts", Integer, nullable=False, server_default="0"),
        Column("error", Text),
        Column("failed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    outbox_processed_events = Table(
        "outbox_processed_events", metadata,
        Column("event_id", String(36), nullable=False),
        Column("consumer", String(200), nullable=False),
        Column("processed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        PrimaryKeyConstraint("event_id", "consumer", name="pk_outbox_processed_events"),
    )
    return {
        "outbox_events": outbox_events,
        "outbox_dead_letters": outbox_dead_letters,
        "outbox_processed_events": outbox_processed_events,
    }
