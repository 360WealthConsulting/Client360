"""seed production role library (360 Wealth Consulting / 360 Tax Solutions)

Seeds the seven firm access profiles that did not previously exist — Senior Tax, Tax Staff,
Accounting, Payroll, Client Service, Reviewer, Read Only — mapping each to capabilities that already
exist in the catalogue. No new capabilities, no schema change, no change to the RBAC engine or its
many-to-many role/capability model. Idempotent: safe to run more than once (ON CONFLICT DO NOTHING).

Profile definitions live in ``app.security.role_library`` so the seed and the tests share one source
of truth. The seven pre-existing profiles (administrator/advisor/operations/benefits_*/insurance_agent/
compliance) are intentionally left untouched.

Revision ID: prodrolelib01
Revises: pv02rtl0vlt1
Create Date: 2026-08-02
"""
import sqlalchemy as sa
from alembic import op

from app.security.role_library import NEW_PROFILES

revision = "prodrolelib01"
down_revision = "pv02rtl0vlt1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insert_role = sa.text(
        "INSERT INTO roles (code, name, description, system_role, active) "
        "VALUES (:code, :name, :description, false, true) ON CONFLICT (code) DO NOTHING")
    link_caps = sa.text(
        "INSERT INTO role_capabilities (role_id, capability_id) "
        "SELECT r.id, c.id FROM roles r, capabilities c "
        "WHERE r.code = :role AND c.code = ANY(:caps) ON CONFLICT DO NOTHING")
    for code, (name, description, caps) in NEW_PROFILES.items():
        bind.execute(insert_role, {"code": code, "name": name, "description": description})
        # Guard: every capability referenced must already exist in the catalogue.
        present = set(bind.execute(
            sa.text("SELECT code FROM capabilities WHERE code = ANY(:caps)"),
            {"caps": list(caps)}).scalars())
        missing = set(caps) - present
        if missing:
            raise RuntimeError(f"role_library profile {code!r} references unknown capabilities: {sorted(missing)}")
        bind.execute(link_caps, {"role": code, "caps": list(caps)})


def downgrade() -> None:
    bind = op.get_bind()
    codes = list(NEW_PROFILES)
    # Remove only the links and the role rows this migration created. Capabilities are shared catalogue
    # entries that predate this migration, so they are never deleted here.
    bind.execute(sa.text(
        "DELETE FROM role_capabilities WHERE role_id IN (SELECT id FROM roles WHERE code = ANY(:codes))"),
        {"codes": codes})
    bind.execute(sa.text("DELETE FROM roles WHERE code = ANY(:codes)"), {"codes": codes})
