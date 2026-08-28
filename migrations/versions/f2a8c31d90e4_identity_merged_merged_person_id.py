"""record merged_person_id on the people.identity_merged contract

The people.summary projection needs the retired person's id to drop its read-model row on every
replay. Runtime validation reads the Python seed, but ``domain_event_contracts`` is the durable,
discoverable catalog and the D.35 seed migration only ever INSERTs when a row is absent — it never
updates one — so the stored payload_schema would otherwise keep documenting the old contract.

Data-only: no table, column, or index is touched.

Revision ID: f2a8c31d90e4
Revises: e1c7a94b2f30
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "f2a8c31d90e4"
down_revision = "e1c7a94b2f30"
branch_labels = None
depends_on = None

_EVENT = "people.identity_merged"
_NEW = {"person_id": "int", "source_contact_count": "int", "merged_person_id": "int"}
_OLD = {"person_id": "int", "source_contact_count": "int"}


def _set_schema(schema):
    op.get_bind().execute(
        sa.text("UPDATE domain_event_contracts SET payload_schema = CAST(:ps AS json) "
                "WHERE event_type = :e"),
        {"ps": json.dumps(schema), "e": _EVENT})


def upgrade():
    _set_schema(_NEW)


def downgrade():
    _set_schema(_OLD)
