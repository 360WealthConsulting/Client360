"""Annual Review Workspace (Phase D.11).

Adds the single new persistence for the Annual Review Workspace — the
``annual_review_sessions`` table — plus the three capabilities that gate it
(``annual_review.read`` / ``.create`` / ``.update``).

The workspace itself is a *composition layer*: it consumes existing services
(Client360, Advisor Intelligence, Advisor Work, Activity Timeline, Compliance,
Meeting Workspace, Portfolio) read-only and never mutates a source-domain record.
The only thing it persists is a review *session* — an advisor-activity record
(notes + a presentation-only checklist) that records that a review happened; it
does not change recommendations, work, timeline, compliance, or portfolio data.

A session is a MUTABLE record (notes and checklist are edited in place), so this
is NOT an append-only ledger — no mutation-blocking trigger. Lifecycle is an
explicit status set (draft / in_progress / completed / archived); no workflow
engine. A partial-unique guard keeps at most one OPEN (draft/in_progress) session
per advisor per client, so "Start review" is idempotent.

Additive and reversible. No source-domain table is touched.
"""
import sqlalchemy as sa
from alembic import op

revision = "i9a1n2r3e4v5"
down_revision = "h2t3i4m5l6n7"
branch_labels = None
depends_on = None

_STATUSES = ("draft", "in_progress", "completed", "archived")

_CAPS = (
    ("annual_review.read", "View the annual review workspace.",
     ("administrator", "advisor", "operations")),
    ("annual_review.create", "Start an annual review session.",
     ("administrator", "advisor")),
    ("annual_review.update", "Update annual review session notes and checklist.",
     ("administrator", "advisor")),
)


def upgrade():
    bind = op.get_bind()
    op.create_table(
        "annual_review_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("person_id", sa.Integer,
                  sa.ForeignKey("people.id", ondelete="CASCADE"), nullable=False),
        sa.Column("household_id", sa.Integer,
                  sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("advisor_id", sa.Integer,
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("checklist_state", sa.dialects.postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('draft','in_progress','completed','archived')",
            name="ck_annual_review_sessions_status"),
    )
    op.create_index("ix_annual_review_sessions_person_id",
                    "annual_review_sessions", ["person_id"])
    op.create_index("ix_annual_review_sessions_advisor_id",
                    "annual_review_sessions", ["advisor_id"])
    op.create_index("ix_annual_review_sessions_status",
                    "annual_review_sessions", ["status"])
    # At most one OPEN review per advisor per client -> idempotent "Start review".
    op.create_index(
        "uq_annual_review_sessions_open", "annual_review_sessions",
        ["person_id", "advisor_id"], unique=True,
        postgresql_where=sa.text("status IN ('draft','in_progress')"))

    for code, description, roles in _CAPS:
        cid = bind.execute(
            sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is None:
            cid = bind.execute(
                sa.text("INSERT INTO capabilities (code, description, sensitive) "
                        "VALUES (:c, :d, false) RETURNING id"),
                {"c": code, "d": description}).scalar()
        for role_code in roles:
            role_id = bind.execute(
                sa.text("SELECT id FROM roles WHERE code = :r"), {"r": role_code}).scalar()
            if role_id is None:
                continue
            exists = bind.execute(
                sa.text("SELECT 1 FROM role_capabilities "
                        "WHERE role_id = :r AND capability_id = :c"),
                {"r": role_id, "c": cid}).scalar()
            if not exists:
                bind.execute(
                    sa.text("INSERT INTO role_capabilities (role_id, capability_id) "
                            "VALUES (:r, :c)"), {"r": role_id, "c": cid})


def downgrade():
    bind = op.get_bind()
    op.drop_index("uq_annual_review_sessions_open", table_name="annual_review_sessions")
    op.drop_index("ix_annual_review_sessions_status", table_name="annual_review_sessions")
    op.drop_index("ix_annual_review_sessions_advisor_id", table_name="annual_review_sessions")
    op.drop_index("ix_annual_review_sessions_person_id", table_name="annual_review_sessions")
    op.drop_table("annual_review_sessions")
    for code, _description, _roles in _CAPS:
        cid = bind.execute(
            sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is not None:
            bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id = :c"),
                         {"c": cid})
            bind.execute(sa.text("DELETE FROM capabilities WHERE id = :c"), {"c": cid})
