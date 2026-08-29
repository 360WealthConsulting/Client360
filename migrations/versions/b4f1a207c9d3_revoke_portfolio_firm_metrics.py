"""revoke portfolio.firm_metrics from every role

360Plus displays assets under management to NO ONE. The AUM values themselves were removed from the
user-facing contracts in the same change (app/services/portfolio.py::_CANONICAL_KEYS is the single
boundary), so this migration closes the capability that used to authorize the firm-metrics surfaces.

Scope, deliberately narrow:
  * REVOKES ``portfolio.firm_metrics`` from every role that currently holds it (administrator today).
  * The capability ROW is retained, not dropped, so the revocation is reversible and the code that
    still gates non-AUM firm triage (cash waiting, missing beneficiaries, accounts without reviews,
    largest household/position BY NAME) keeps a meaningful gate.
  * ``vault.category.wealth`` is NOT touched. It governs wealth-category DOCUMENTS (one of eight
    parallel vault categories) and exposes no portfolio value; removing it would block legitimate
    investment paperwork and would not affect AUM visibility at all.
  * ``analytics.executive`` is NOT revoked. Only the AUM metric and the two AUM widgets were
    removed; every other executive analytic still needs that capability.

Data-only: no table, column, index, or constraint is touched.

Revision ID: b4f1a207c9d3
Revises: a3c7e19b45d2
"""
import sqlalchemy as sa
from alembic import op

revision = "b4f1a207c9d3"
down_revision = "a3c7e19b45d2"
branch_labels = None
depends_on = None

CODE = "portfolio.firm_metrics"
#: Restored on downgrade — the assignment that existed when this migration was written.
_PRIOR_ROLES = ("administrator",)


def upgrade():
    bind = op.get_bind()
    cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": CODE}).scalar()
    if cid is None:
        return
    bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id = :c"), {"c": cid})


def downgrade():
    bind = op.get_bind()
    cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": CODE}).scalar()
    if cid is None:
        return
    for role_code in _PRIOR_ROLES:
        rid = bind.execute(sa.text("SELECT id FROM roles WHERE code = :r"), {"r": role_code}).scalar()
        if rid is None:
            continue
        exists = bind.execute(
            sa.text("SELECT 1 FROM role_capabilities WHERE role_id = :r AND capability_id = :c"),
            {"r": rid, "c": cid}).scalar()
        if not exists:
            bind.execute(
                sa.text("INSERT INTO role_capabilities (role_id, capability_id) VALUES (:r, :c)"),
                {"r": rid, "c": cid})
