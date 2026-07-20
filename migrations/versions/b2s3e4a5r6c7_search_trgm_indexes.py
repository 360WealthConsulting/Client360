"""Search performance — pg_trgm GIN indexes on source_contacts (Sprint 2, D-3).

Global search runs ``ILIKE '%term%'`` across ``source_contacts`` name/email/phone/city columns.
Leading-wildcard ILIKE cannot use a b-tree index and forces a sequential scan, which degrades as
the contact table grows. This migration installs the ``pg_trgm`` extension and GIN trigram
indexes (``gin_trgm_ops``) on the searched columns, which Postgres *can* use for ``ILIKE
'%term%'`` — turning the search hotspot from a full scan into an index scan.

Additive, idempotent, and reversible: no table is altered; only the extension and indexes are
added. Downgrade drops the indexes and leaves the ``pg_trgm`` extension in place (dropping a
shared extension could break unrelated objects; removing the indexes fully reverts this change's
schema footprint).
"""
from alembic import op

revision = "b2s3e4a5r6c7"
down_revision = "a1n2o3t4e5s6"
branch_labels = None
depends_on = None

_INDEXES = {
    "ix_source_contacts_full_name_trgm": "full_name",
    "ix_source_contacts_first_name_trgm": "first_name",
    "ix_source_contacts_last_name_trgm": "last_name",
    "ix_source_contacts_email_trgm": "email",
    "ix_source_contacts_phone_trgm": "phone",
    "ix_source_contacts_city_trgm": "city",
}


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for index_name, column in _INDEXES.items():
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} "
            f"ON source_contacts USING gin ({column} gin_trgm_ops)"
        )


def downgrade():
    for index_name in _INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
    # pg_trgm extension is intentionally left installed (may be shared).
