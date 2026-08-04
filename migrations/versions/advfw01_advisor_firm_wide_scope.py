"""Advisor firm-wide record visibility — all staff SEE all clients.

Grants the EXISTING ``record.read_all`` capability to the ``advisor`` role. This encodes the office's
operating decision that every staff member (advisor) can SEE the entire client book, not only a
personally-assigned book.

Before this, ``record.read_all`` was held by ``administrator`` + ``compliance``, so an advisor was
read-scoped to their assigned records: the firm-wide collection screens (global Search, People,
Households, Home, Timeline) returned 403 and the advisor could reach only assigned records. Granting
this one read capability lets an advisor browse and search the whole book.

Deliberately scoped to READ only. ``record.write_all`` is NOT granted: it is an administrator-only
control-plane capability (see ``app/security/role_library.ADMINISTRATOR_ONLY``), so firm-wide WRITE is
out of scope for this change. Advisors continue to add notes/tasks and edit within their assigned
record scope; whether any advisor may WRITE any client firm-wide is a separate security decision that
must not be made by silently crossing the admin-only boundary. ``record.read_all`` is not an
administrator-only capability (``compliance`` already holds it), so this is not a promotion to
administrator — no ``identity.manage`` / ``role.manage`` / ``record.write_all`` is granted.

No new capability and no schema change: this inserts a single ``role_capabilities`` row (idempotently)
associating an already-defined capability with the already-defined ``advisor`` role. Additive and
reversible — the down migration removes exactly the grant it added.

Revision ID: advfw01
Revises: pmh01
Create Date: 2026-08-04
"""
import sqlalchemy as sa
from alembic import op

revision = "advfw01"
down_revision = "pmh01"
branch_labels = None
depends_on = None

ROLE = "advisor"
CAPS = ("record.read_all",)


def upgrade() -> None:
    bind = op.get_bind()
    role_id = bind.execute(sa.text("SELECT id FROM roles WHERE code = :r"), {"r": ROLE}).scalar()
    if role_id is None:
        return
    for code in CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is None:
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


def downgrade() -> None:
    bind = op.get_bind()
    role_id = bind.execute(sa.text("SELECT id FROM roles WHERE code = :r"), {"r": ROLE}).scalar()
    if role_id is None:
        return
    for code in CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is None:
            continue
        bind.execute(
            sa.text("DELETE FROM role_capabilities WHERE role_id = :r AND capability_id = :c"),
            {"r": role_id, "c": cid},
        )
