"""Evidence write-once store (F3.3 / Epic 3).

Additive and reversible. Creates the canonical ``evidence`` table — an immutable,
reference-only store for regulatory/operational evidence associated with
workflows — and enforces write-once at the database level with the same
append-only trigger pattern used by ``audit_events`` (F3.1). No binary document
content is stored (reference + checksum + metadata only). Preserves all existing
audit functionality. Single Alembic head preserved.

Idempotent DDL (IF NOT EXISTS / IF EXISTS) for robustness, consistent with the
F3.2 migration. The table is intentionally NOT declared in identity_tables.py /
schema.py; app.db reflects it at runtime (see docs/DATABASE.md).
"""
import sqlalchemy as sa
from alembic import op

revision = "f3d4e5v6i7d8"
down_revision = "f2h3c4a5i6n7"
branch_labels = None
depends_on = None


def upgrade():
    if "evidence" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "evidence",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("evidence_uid", sa.String(length=36), nullable=False),
            sa.Column("evidence_type", sa.String(length=100), nullable=False),
            sa.Column("classification", sa.String(length=50), server_default="operational", nullable=False),
            sa.Column("source", sa.String(length=150), nullable=False),
            sa.Column("checksum", sa.String(length=128), nullable=True),
            sa.Column("reference", sa.Text(), nullable=True),
            sa.Column("evidence_metadata", sa.JSON(), server_default="{}", nullable=False),
            sa.Column("provenance", sa.Text(), nullable=True),
            sa.Column("audit_event_id", sa.Integer(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("evidence_uid", name="uq_evidence_uid"),
            sa.ForeignKeyConstraint(["audit_event_id"], ["audit_events.id"], ondelete="SET NULL"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_evidence_audit_event ON evidence (audit_event_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_evidence_created_at ON evidence (created_at)")
    # Write-once enforcement (same pattern as audit_events_immutable, F3.1).
    op.execute(
        "CREATE OR REPLACE FUNCTION prevent_evidence_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'evidence records are write-once'; END; $$ LANGUAGE plpgsql"
    )
    op.execute("DROP TRIGGER IF EXISTS evidence_immutable ON evidence")
    op.execute(
        "CREATE TRIGGER evidence_immutable BEFORE UPDATE OR DELETE ON evidence "
        "FOR EACH ROW EXECUTE FUNCTION prevent_evidence_mutation()"
    )


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS evidence_immutable ON evidence")
    op.execute("DROP FUNCTION IF EXISTS prevent_evidence_mutation()")
    op.execute("DROP INDEX IF EXISTS ix_evidence_created_at")
    op.execute("DROP INDEX IF EXISTS ix_evidence_audit_event")
    op.execute("DROP TABLE IF EXISTS evidence")
