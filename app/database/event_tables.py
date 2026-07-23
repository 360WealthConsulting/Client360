"""Declared schema for the Phase D.34 Enterprise Domain Event Model.

Mirrors the live schema created by migration ``zb1c2d3e4f5a``. D.34 standardizes a typed, versioned
**domain-event model** OVER the existing transactional outbox (``app/platform/outbox.py`` +
``app/platform/events.py``) — it reuses the one outbox as the internal event bus and does NOT add a
second event table (the architecture invariant): domain events are contract-validated envelopes written
to ``outbox_events``. The only persistence D.34 adds is **discovery/governance metadata**:

- ``domain_event_contracts`` — the typed contract registry: the event type, category, version,
  lifecycle status, owner, producer, and the declared payload schema (a references-only contract).
- ``domain_event_subscriptions`` — the durable subscription registry: which consumer subscribes to
  which event type (the live outbox subscription is registered at startup; this is the discoverable +
  governable record so orphan subscriptions / producers-without-consumers are detectable).

These own no event data (the outbox owns the event log) and perform no delivery (the outbox dispatcher
delivers) — they record the *model* the events conform to.
"""
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
)

# Lifecycle status of a registered domain-event contract.
# active     — a live contract; producers may publish it and consumers may subscribe
# deprecated — superseded by a newer version/contract; retained for one release
# retired    — removed from the event model
CONTRACT_STATUSES = ("active", "deprecated", "retired")
SUBSCRIPTION_STATUSES = ("active", "inactive")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def define_event_tables(metadata: MetaData):
    contracts = Table(
        "domain_event_contracts", metadata,
        Column("id", Integer, primary_key=True),
        Column("event_type", Text, nullable=False, unique=True),
        Column("category", Text, nullable=False),
        Column("name", Text, nullable=False),
        Column("description", Text),
        Column("status", Text, nullable=False, server_default="active"),
        Column("schema_version", Integer, nullable=False, server_default="1"),
        Column("owner", Text),
        Column("producer", Text),               # the producing subsystem, e.g. "orchestration.engine"
        Column("payload_schema", JSON),         # {field: type} — a references-only contract (no PII)
        Column("depends_on", JSON),             # other event types this one composes/causes
        Column("deprecated_at", DateTime(timezone=True)),
        Column("deprecated_reason", Text),
        Column("contract_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", CONTRACT_STATUSES), name="ck_domain_event_contract_status"),
    )

    subscriptions = Table(
        "domain_event_subscriptions", metadata,
        Column("id", Integer, primary_key=True),
        Column("event_type", Text, nullable=False),
        Column("consumer", Text, nullable=False),   # the subscribing consumer, e.g. "notification.dispatch"
        Column("status", Text, nullable=False, server_default="active"),
        Column("owner", Text),
        Column("description", Text),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("event_type", "consumer", name="uq_domain_event_subscription"),
        CheckConstraint(_in("status", SUBSCRIPTION_STATUSES), name="ck_domain_event_subscription_status"),
    )
    return {"domain_event_contracts": contracts, "domain_event_subscriptions": subscriptions}
