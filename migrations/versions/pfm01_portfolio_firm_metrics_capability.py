"""Portfolio firm-metrics capability (privacy fix).

Seeds a dedicated, sensitive capability ``portfolio.firm_metrics`` that gates firm-wide financial
aggregates (firm AUM, total account value, cash waiting to invest, largest household, largest position,
and their sibling firm-wide portfolio counts) on the Firm Dashboard, ``/api/stats``, and ``/wealth``.

Firm-metric visibility is INDEPENDENTLY controllable: it is granted here ONLY to the administrator
profile, so an employee does NOT see firm AUM merely because they hold ``record.read_all`` (the broad
record-scope capability). Leadership/partners who should see firm metrics are granted this capability
per-role or per-user through the existing role/capability administration — no username checks.

Additive and reversible; no data table, index, or trigger. No client ownership or record assignment is
touched.
"""
import sqlalchemy as sa
from alembic import op

revision = "pfm01"
down_revision = "lnkg01"
branch_labels = None
depends_on = None

CODE = "portfolio.firm_metrics"
DESCRIPTION = ("View firm-wide portfolio aggregates (firm AUM, total account value, cash waiting, "
               "largest household, largest position).")
_ROLES = ("administrator",)


def upgrade():
    bind = op.get_bind()
    cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": CODE}).scalar()
    if cid is None:
        cid = bind.execute(
            sa.text("INSERT INTO capabilities (code, description, sensitive) "
                    "VALUES (:c, :d, true) RETURNING id"),
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
