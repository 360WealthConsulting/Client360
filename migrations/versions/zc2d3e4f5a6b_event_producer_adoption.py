"""Enterprise Domain Event Producer Adoption (Phase D.35).

Expands the governed D.34 domain-event model beyond orchestration: the major business domains now
publish typed, past-tense domain FACTS through the existing standardized publisher + transactional
outbox. This migration is metadata-only and ADDITIVE — it registers the new typed contracts (and their
dark-launched read-model subscriptions) into ``domain_event_contracts`` / ``domain_event_subscriptions``.

It adds NO new tables, NO event log (the outbox remains the sole bus and the sole event log), and NO
schema change to the event tables. Contracts are references-only (ids/codes/statuses); the publisher +
governance reject any prohibited sensitive field. Reuses the existing D.26 ``observability.*``
capabilities (no new capabilities, no RBAC changes). Reversible. Single Alembic head (down
``zb1c2d3e4f5a``).
"""
import json

import sqlalchemy as sa
from alembic import op

from app.database.event_seed import D35_CONTRACTS_SEED

revision = "zc2d3e4f5a6b"
down_revision = "zb1c2d3e4f5a"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for d in D35_CONTRACTS_SEED:
        if bind.execute(sa.text("SELECT id FROM domain_event_contracts WHERE event_type=:e"),
                        {"e": d["event_type"]}).scalar() is None:
            bind.execute(sa.text(
                "INSERT INTO domain_event_contracts "
                "(event_type, category, name, description, status, schema_version, owner, producer, "
                " payload_schema, depends_on) "
                "VALUES (:e, :cat, :n, :desc, 'active', :v, :o, :p, CAST(:ps AS json), CAST(:dep AS json))"),
                {"e": d["event_type"], "cat": d["category"], "n": d["name"], "desc": d.get("description"),
                 "v": d["schema_version"], "o": d.get("owner"), "p": d["producer"],
                 "ps": json.dumps(d["payload_schema"]), "dep": json.dumps(d.get("depends_on") or [])})
        for consumer in d.get("subscribers", []):
            if bind.execute(sa.text(
                    "SELECT id FROM domain_event_subscriptions WHERE event_type=:e AND consumer=:c"),
                    {"e": d["event_type"], "c": consumer}).scalar() is None:
                bind.execute(sa.text(
                    "INSERT INTO domain_event_subscriptions (event_type, consumer, status, owner, description) "
                    "VALUES (:e, :c, 'active', :o, :d)"),
                    {"e": d["event_type"], "c": consumer, "o": d.get("owner"),
                     "d": f"Read-model projection of {d['event_type']}."})


def downgrade():
    bind = op.get_bind()
    for d in D35_CONTRACTS_SEED:
        bind.execute(sa.text("DELETE FROM domain_event_subscriptions WHERE event_type=:e"),
                     {"e": d["event_type"]})
        bind.execute(sa.text("DELETE FROM domain_event_contracts WHERE event_type=:e"),
                     {"e": d["event_type"]})
