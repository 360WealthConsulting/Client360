"""insurance policy lifecycle statuses: issued, delivered, reinstated (Release 0.10.0, Phase 1)

Widens the insurance_policies status CHECK to cover the full new-business →
in-force → reinstatement lifecycle, so each transition can publish a proportional
Timeline/Audit event. Additive and reversible; single Alembic head.
"""
import sqlalchemy as sa
from alembic import op

revision = "x4d5f6h7c8e9"
down_revision = "w3c4e5g6b7d8"
branch_labels = None
depends_on = None

OLD = ("proposed", "applied", "underwriting", "in_force",
       "lapsed", "surrendered", "replaced", "death_claim")
NEW = ("proposed", "applied", "underwriting", "issued", "delivered", "in_force",
       "reinstated", "lapsed", "surrendered", "replaced", "death_claim")


def _check(allowed):
    return "status IN (" + ", ".join(f"'{v}'" for v in allowed) + ")"


def upgrade():
    op.drop_constraint("ck_ins_policy_status", "insurance_policies", type_="check")
    op.create_check_constraint("ck_ins_policy_status", "insurance_policies", _check(NEW))


def downgrade():
    # Roll back any rows on statuses the narrowed CHECK would reject.
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE insurance_policies SET status='underwriting' "
                         "WHERE status IN ('issued','delivered','reinstated')"))
    op.drop_constraint("ck_ins_policy_status", "insurance_policies", type_="check")
    op.create_check_constraint("ck_ins_policy_status", "insurance_policies", _check(OLD))
