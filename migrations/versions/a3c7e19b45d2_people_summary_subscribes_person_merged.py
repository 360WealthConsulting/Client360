"""people.summary subscribes to people.person_merged

The projection engine reads its subscription list from the Python seed, but
``projection_definitions`` is the durable, discoverable registry and the D.36 seed migration only
INSERTs when a row is absent — it never updates one — so the stored subscribed_events would keep
documenting the old set.

Data-only: no table, column, or index is touched. schema_version is deliberately NOT bumped —
the read-model shape is unchanged, only the set of events it consumes.

Revision ID: a3c7e19b45d2
Revises: f2a8c31d90e4
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "a3c7e19b45d2"
down_revision = "f2a8c31d90e4"
branch_labels = None
depends_on = None

_PROJECTION = "people.summary"
_NEW = ["people.person_created", "people.person_updated", "people.identity_merged",
        "people.person_merged"]
_OLD = ["people.person_created", "people.person_updated", "people.identity_merged"]


def _set_events(events):
    op.get_bind().execute(
        sa.text("UPDATE projection_definitions SET subscribed_events = CAST(:e AS json) "
                "WHERE projection_id = :p"),
        {"e": json.dumps(events), "p": _PROJECTION})


def upgrade():
    _set_events(_NEW)


def downgrade():
    _set_events(_OLD)
