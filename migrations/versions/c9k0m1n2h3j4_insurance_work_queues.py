"""Insurance work queues (Release 0.10.0, Phase 6 — reuse Work Management).

Data-only, reversible. Seeds data-driven **insurance** work queues consumed by the EXISTING
Work Management ``queue_items`` / ``queue_matches`` filters — there is no insurance-specific
queue engine, model, or UI logic. Insurance exceptions (raised by the shared Exception Engine
with ``domain='insurance'``) project through the same ``work_items`` surface as tax and benefits.

No schema change; single Alembic head preserved. No new scheduler, queue, or assignment
framework — Phase 6 orchestrates and wires the existing platform subsystems only.
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "c9k0m1n2h3j4"
down_revision = "b8i9k1l2g3j4"
branch_labels = None
depends_on = None

_REVIEWS = ["INS_REVIEW_OVERDUE"]
_LICENSING = ["INS_LICENSE_EXPIRING", "INS_CE_PERIOD_ENDING"]
_COMMISSIONS = ["INS_COMMISSION_VARIANCE", "INS_COMMISSION_OUTSTANDING"]

# (code, name, description, criteria, required_capability)
INSURANCE_QUEUES = [
    ("insurance_unassigned", "Insurance — Unassigned", "Insurance work not yet assigned",
     {"domain": "insurance", "entity_type": "exception", "unassigned": True}, "insurance.read"),
    ("insurance_exceptions", "Insurance — Exceptions", "All open insurance exceptions",
     {"domain": "insurance", "entity_type": "exception"}, "insurance.read"),
    ("insurance_reviews", "Insurance — In-Force Reviews", "Overdue in-force policy reviews",
     {"domain": "insurance", "codes": _REVIEWS}, "insurance.read"),
    ("insurance_licensing", "Insurance — Licensing and CE", "Producer license / CE expiry reminders",
     {"domain": "insurance", "codes": _LICENSING}, "insurance.read"),
    ("insurance_commissions", "Insurance — Commissions", "Commission variance and outstanding reconciliation",
     {"domain": "insurance", "codes": _COMMISSIONS}, "insurance.read"),
    ("insurance_high_priority", "Insurance — High Priority or Blockers", "Blocker/high-severity insurance work",
     {"domain": "insurance", "entity_type": "exception", "severity": ["blocker", "high"]}, "insurance.read"),
]


def upgrade():
    bind = op.get_bind()
    for code, name, description, criteria, cap in INSURANCE_QUEUES:
        bind.execute(sa.text(
            "INSERT INTO work_queues (code, name, description, criteria, required_capability) "
            "VALUES (:code, :name, :description, CAST(:criteria AS json), :cap)"),
            {"code": code, "name": name, "description": description,
             "criteria": json.dumps(criteria), "cap": cap})


def downgrade():
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM work_queues WHERE code IN :codes")
                 .bindparams(sa.bindparam("codes", expanding=True)),
                 {"codes": [q[0] for q in INSURANCE_QUEUES]})
