"""Audit hash-chain columns (F3.2 / Epic 3, ADR-015 Option A).

Additive and reversible. Adds nullable hash-chain columns to ``audit_events`` so
the append-only audit log (F3.1) becomes tamper-evident. Existing rows are
untouched and remain valid but **unchained** (all hash columns NULL) — no
backfill, per ADR-015. The first record written after this migration becomes the
documented genesis of its chain. No change to the append-only trigger. Single
Alembic head preserved.
"""
from alembic import op

revision = "f2h3c4a5i6n7"
down_revision = "e1m2o3x4b5t6"
branch_labels = None
depends_on = None


def upgrade():
    # Idempotent DDL (ADD COLUMN / CREATE INDEX IF NOT EXISTS): additive and safe
    # even if an earlier step created the columns from the declared metadata
    # (migration c410f4a1b2c3 builds audit_events via metadata.tables[...].create()).
    # The columns are intentionally NOT declared in identity_tables.py so a fresh
    # build does not pre-create them; this guard makes the migration robust either
    # way. Nullable, no server_default: existing rows stay NULL (unchained); new
    # rows are populated by write_audit_event.
    op.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS prev_hash TEXT")
    op.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS entry_hash TEXT")
    op.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS hash_version INTEGER")
    op.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS chain_id TEXT")
    # Walk a chain in order.
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_events_chain ON audit_events (chain_id, id)")
    # Entry hashes are unique among chained rows (legacy NULLs excluded).
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_audit_events_entry_hash "
        "ON audit_events (entry_hash) WHERE entry_hash IS NOT NULL"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS uq_audit_events_entry_hash")
    op.execute("DROP INDEX IF EXISTS ix_audit_events_chain")
    op.execute("ALTER TABLE audit_events DROP COLUMN IF EXISTS chain_id")
    op.execute("ALTER TABLE audit_events DROP COLUMN IF EXISTS hash_version")
    op.execute("ALTER TABLE audit_events DROP COLUMN IF EXISTS entry_hash")
    op.execute("ALTER TABLE audit_events DROP COLUMN IF EXISTS prev_hash")
