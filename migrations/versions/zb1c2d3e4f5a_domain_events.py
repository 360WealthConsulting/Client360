"""Enterprise Domain Event Model, Event Registry, Contracts, Governance & Diagnostics (Phase D.34).

D.34 standardizes a typed, versioned **domain-event model** OVER the existing transactional outbox
(``app/platform/outbox.py`` + ``app/platform/events.py``). It **reuses the one outbox as the internal
event bus** — delivery guarantees, idempotency, dead-letter, and envelope versioning already exist — and
adds no second event table (the architecture invariant): domain events are contract-validated envelopes
written to ``outbox_events``. The only persistence D.34 adds is discovery/governance metadata:
``domain_event_contracts`` (the typed contract registry) and ``domain_event_subscriptions`` (the
durable subscription registry). Seeds the contracts for the event flows that already exist (workflow +
runtime coordination) plus the new ``orchestration.lifecycle`` event.

Reuses the existing D.26 ``observability.*`` capabilities (no new capabilities, no RBAC changes).
Additive and reversible. Single Alembic head (down ``za0b1c2d3e4f``).
"""
import json

import sqlalchemy as sa
from alembic import op

from app.database.event_seed import DOMAIN_EVENT_CONTRACTS_SEED, DOMAIN_EVENT_SUBSCRIPTIONS_SEED

revision = "zb1c2d3e4f5a"
down_revision = "za0b1c2d3e4f"
branch_labels = None
depends_on = None

_CONTRACT_STATUSES = ("active", "deprecated", "retired")
_SUBSCRIPTION_STATUSES = ("active", "inactive")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def upgrade():
    bind = op.get_bind()
    op.create_table(
        "domain_event_contracts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_type", sa.Text, nullable=False, unique=True),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("schema_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("owner", sa.Text),
        sa.Column("producer", sa.Text),
        sa.Column("payload_schema", sa.JSON),
        sa.Column("depends_on", sa.JSON),
        sa.Column("deprecated_at", sa.DateTime(timezone=True)),
        sa.Column("deprecated_reason", sa.Text),
        sa.Column("contract_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _CONTRACT_STATUSES), name="ck_domain_event_contract_status"),
    )
    op.create_table(
        "domain_event_subscriptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("consumer", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("owner", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("event_type", "consumer", name="uq_domain_event_subscription"),
        sa.CheckConstraint(_in("status", _SUBSCRIPTION_STATUSES), name="ck_domain_event_subscription_status"),
    )

    for event_type, category, name, producer, version, payload_schema, depends_on, desc in \
            DOMAIN_EVENT_CONTRACTS_SEED:
        if bind.execute(sa.text("SELECT id FROM domain_event_contracts WHERE event_type=:e"),
                        {"e": event_type}).scalar() is None:
            bind.execute(sa.text(
                "INSERT INTO domain_event_contracts "
                "(event_type, category, name, description, status, schema_version, producer, "
                " payload_schema, depends_on) "
                "VALUES (:e, :cat, :n, :desc, 'active', :v, :p, CAST(:ps AS json), CAST(:dep AS json))"),
                {"e": event_type, "cat": category, "n": name, "desc": desc, "v": version, "p": producer,
                 "ps": json.dumps(payload_schema), "dep": json.dumps(depends_on)})

    for event_type, consumer, owner, desc in DOMAIN_EVENT_SUBSCRIPTIONS_SEED:
        if bind.execute(sa.text(
                "SELECT id FROM domain_event_subscriptions WHERE event_type=:e AND consumer=:c"),
                {"e": event_type, "c": consumer}).scalar() is None:
            bind.execute(sa.text(
                "INSERT INTO domain_event_subscriptions (event_type, consumer, status, owner, description) "
                "VALUES (:e, :c, 'active', :o, :d)"),
                {"e": event_type, "c": consumer, "o": owner, "d": desc})


def downgrade():
    op.drop_table("domain_event_subscriptions")
    op.drop_table("domain_event_contracts")
