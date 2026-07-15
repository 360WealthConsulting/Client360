"""exception queue criteria (Sprint 5.5 Phase 5)

Revision ID: q7b58f6c5d4e
Revises: p6a47e5d4f3b

Release 0.9.10 (Sprint 5.5), Phase 5 — Work Management integration. Renames the two
generic exception queues seeded in Phase 1 (exceptions → tax_exceptions,
exceptions_critical → tax_exceptions_critical) and sets criteria on all three exception
queues (seeded with an empty placeholder in Phase 1) now that the exception work-item
projection exists, so the queues filter exception work items via the standard
`work_intelligence.queue_matches` mechanism. Data-only; reversible; single head.
"""
from alembic import op
import sqlalchemy as sa

revision = "q7b58f6c5d4e"
down_revision = "p6a47e5d4f3b"
branch_labels = None
depends_on = None

# (current code, target code, criteria)
QUEUES = [
    ("exceptions", "tax_exceptions", '{"entity_type": "exception"}'),
    ("exceptions_critical", "tax_exceptions_critical", '{"entity_type": "exception", "severity": ["blocker", "high"]}'),
    ("compliance_exceptions", "compliance_exceptions", '{"entity_type": "exception", "category": "compliance"}'),
]


def upgrade():
    bind = op.get_bind()
    for old_code, new_code, criteria in QUEUES:
        bind.execute(sa.text("UPDATE work_queues SET code = :new, criteria = CAST(:c AS json) WHERE code = :old"),
                     {"new": new_code, "c": criteria, "old": old_code})


def downgrade():
    bind = op.get_bind()
    for old_code, new_code, _criteria in QUEUES:
        bind.execute(sa.text("UPDATE work_queues SET code = :old, criteria = CAST('{}' AS json) WHERE code = :new"),
                     {"old": old_code, "new": new_code})
