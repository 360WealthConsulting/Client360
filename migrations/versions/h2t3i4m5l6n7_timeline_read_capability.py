"""Timeline read capability (Phase D.10).

Seeds only the ``timeline.read`` capability that gates the client/household activity
timeline (a read-only projection over existing authoritative records). No timeline table
is created and no history is persisted or backfilled — the timeline reads
``timeline_events`` / ``advisor_work_events`` / ``compliance_*`` directly. The capability
is granted to the roles that already hold ``client.read`` (administrator, advisor,
compliance, operations); it is NOT a bypass around advisor_work.read / compliance.review.read
(the timeline service enforces those per source and redacts accordingly).

Additive and reversible; no data table, index, or trigger.
"""
import sqlalchemy as sa
from alembic import op

revision = "h2t3i4m5l6n7"
down_revision = "g1w2o3r4k5m6"
branch_labels = None
depends_on = None

CODE = "timeline.read"
DESCRIPTION = "View the client/household activity timeline."
_ROLES = ("administrator", "advisor", "compliance", "operations")


def upgrade():
    bind = op.get_bind()
    cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": CODE}).scalar()
    if cid is None:
        cid = bind.execute(
            sa.text("INSERT INTO capabilities (code, description, sensitive) "
                    "VALUES (:c, :d, false) RETURNING id"),
            {"c": CODE, "d": DESCRIPTION},
        ).scalar()
    for role_code in _ROLES:
        role_id = bind.execute(sa.text("SELECT id FROM roles WHERE code = :r"), {"r": role_code}).scalar()
        if role_id is None:
            continue
        exists = bind.execute(
            sa.text("SELECT 1 FROM role_capabilities WHERE role_id = :r AND capability_id = :c"),
            {"r": role_id, "c": cid},
        ).scalar()
        if not exists:
            bind.execute(
                sa.text("INSERT INTO role_capabilities (role_id, capability_id) VALUES (:r, :c)"),
                {"r": role_id, "c": cid},
            )


def downgrade():
    bind = op.get_bind()
    cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": CODE}).scalar()
    if cid is not None:
        bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id = :c"), {"c": cid})
        bind.execute(sa.text("DELETE FROM capabilities WHERE id = :c"), {"c": cid})
