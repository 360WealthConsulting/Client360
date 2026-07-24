"""Compliance supervise capability (Phase D.47).

Seeds only the ``compliance.supervise`` capability that gates the Enterprise Compliance Intelligence &
Supervisory Operations layer — a READ-ONLY composition over the existing authoritative compliance, review,
exception, audit, and approval services. No table, index, or trigger is created and no state is persisted:
the supervisory layer reads ``compliance_reviews`` / ``exceptions`` / the audit hash-chain / annual-review
sessions / producer licensing directly, and never mutates.

The capability is SENSITIVE and granted only to the roles that already act as the compliance supervisor
(administrator, compliance) — the ``advisor`` role does NOT hold it, which is the supervisor-vs-advisor
visibility boundary. It is not a bypass around any owning capability (the composed reads still enforce
``compliance.review.read`` / ``audit.read`` / record scope per source).

Additive and reversible; no data table. Single Alembic head (down ``m4p5o6r7t8c9``).
"""
import sqlalchemy as sa
from alembic import op

revision = "n5s6u7p8v9w0"
down_revision = "m4p5o6r7t8c9"
branch_labels = None
depends_on = None

CODE = "compliance.supervise"
DESCRIPTION = "Access the supervisory compliance-operations workspace (read-only oversight)."
_ROLES = ("administrator", "compliance")


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
