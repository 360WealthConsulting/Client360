"""Insurance operational-scan capability (Release 0.10.0, pre-Phase-7 cleanup).

Data-only, reversible. Introduces a dedicated ``insurance.scan`` capability so the operational
detector scan has a purpose-built authorization instead of overloading ``insurance.write`` — a
cleaner long-term model (running a non-mutating detection sweep is a distinct authority from
writing insurance records).

Granted to exactly the roles that can run the (unified/reviews/commissions) scan today —
administrator, insurance_agent, insurance_operations — so this **neither expands nor weakens**
authorization; it only names the authority explicitly. The producer-licensing scan keeps its
tighter ``insurance.licensing.write`` gate (see app/routes/insurance.py), so licensing oversight
is not broadened.

No schema change; single Alembic head preserved.
"""
import sqlalchemy as sa
from alembic import op

revision = "d0l1n2o3i4k5"
down_revision = "c9k0m1n2h3j4"
branch_labels = None
depends_on = None

_SCAN_ROLES = ("administrator", "insurance_agent", "insurance_operations")


def upgrade():
    bind = op.get_bind()
    bind.execute(sa.text(
        "INSERT INTO capabilities (code, description, sensitive) "
        "VALUES ('insurance.scan', 'Run the operational insurance detector scan', false) "
        "ON CONFLICT (code) DO NOTHING"))
    bind.execute(sa.text(
        "INSERT INTO role_capabilities (role_id, capability_id) "
        "SELECT r.id, c.id FROM roles r CROSS JOIN capabilities c "
        "WHERE c.code = 'insurance.scan' AND r.code IN :roles "
        "ON CONFLICT DO NOTHING").bindparams(sa.bindparam("roles", expanding=True)),
        {"roles": list(_SCAN_ROLES)})


def downgrade():
    bind = op.get_bind()
    bind.execute(sa.text(
        "DELETE FROM role_capabilities WHERE capability_id IN "
        "(SELECT id FROM capabilities WHERE code = 'insurance.scan')"))
    bind.execute(sa.text("DELETE FROM capabilities WHERE code = 'insurance.scan'"))
