"""Seed the client-portal runtime gates as governed D.27 metadata.

``app/portal/gate.py`` evaluates every portal gate through the runtime engine with a production-safe
``default=False``. Until now none of those gates existed as runtime metadata, so every evaluation fell
through to the hard-coded default and the gates were indistinguishable from an unresolvable runtime — the
values could not be governed, audited, or observed. This migration seeds the eight portal feature
definitions and the two portal configuration items so the runtime snapshot becomes their authoritative
source.

Behavior-preserving by construction: every seeded value equals the ``GATES`` default it replaces — all
eight features are seeded ``active`` but DISABLED at rollout 0, ``portal.mfa_required`` true and
``portal.production_signed_off`` false. The portal therefore stays exactly as closed as it was before,
and the local identity provider stays registered (it is gated on ``portal.production_signed_off``).

D.27 remains the sole metadata owner; the runtime engine remains the sole evaluator. Additive, idempotent
(every insert is guarded by a code lookup), and reversible. The shared ``runtime-defaults`` configuration
set is created only when absent and is NEVER removed on downgrade — it is shared with
``z8a9b0c1d2e3_runtime_authority``. Single Alembic head (down ``docdisp01``).
"""
import sqlalchemy as sa
from alembic import op

revision = "9483fa25e622"
down_revision = "docdisp01"
branch_labels = None
depends_on = None

# Portal feature definitions. Seeded ON the registry but OFF as behavior (status active + enabled false
# + rollout 0 → evaluates enabled=False), matching app/portal/gate.py GATES exactly.
_FLAGS = (
    "portal.enabled",
    "portal.household_enabled",
    "portal.documents.download_enabled",
    "portal.documents.upload_enabled",
    "portal.messaging_enabled",
    "portal.appointments_enabled",
    "portal.financial_summary_enabled",
    "portal.forms_enabled",
)

# Portal configuration items. (code, value_type, json_value) — values equal the GATES defaults.
_ITEMS = (
    ("portal.mfa_required", "boolean", "true"),
    ("portal.production_signed_off", "boolean", "false"),
)


def upgrade():
    bind = op.get_bind()

    # --- seed the portal feature definitions (disabled; governance intent only) ---
    for code in _FLAGS:
        if bind.execute(sa.text("SELECT id FROM configuration_feature_flags WHERE code=:c"),
                        {"c": code}).scalar() is None:
            bind.execute(sa.text(
                "INSERT INTO configuration_feature_flags (code, name, status, enabled, rollout_percentage) "
                "VALUES (:c, :c, 'active', false, 0)"), {"c": code})

    # --- seed the portal configuration items into the SHARED runtime-defaults set ---
    set_id = bind.execute(sa.text("SELECT id FROM configuration_sets WHERE code='runtime-defaults'")).scalar()
    if set_id is None:
        set_id = bind.execute(sa.text(
            "INSERT INTO configuration_sets (code, name, status) "
            "VALUES ('runtime-defaults', 'Runtime Defaults', 'active') RETURNING id")).scalar()
    for code, vtype, jval in _ITEMS:
        if bind.execute(sa.text("SELECT id FROM configuration_items WHERE code=:c"), {"c": code}).scalar() is None:
            bind.execute(sa.text(
                "INSERT INTO configuration_items (set_id, code, name, value_type, value, status, version) "
                "VALUES (:s, :c, :c, :vt, CAST(:v AS json), 'active', 1)"),
                {"s": set_id, "c": code, "vt": vtype, "v": jval})


def downgrade():
    bind = op.get_bind()
    # Remove ONLY the portal rows this migration introduced. The runtime-defaults configuration set is
    # shared with z8a9b0c1d2e3_runtime_authority and is deliberately left in place.
    for code in _FLAGS:
        bind.execute(sa.text("DELETE FROM configuration_feature_flags WHERE code=:c"), {"c": code})
    for code, _vtype, _jval in _ITEMS:
        bind.execute(sa.text("DELETE FROM configuration_items WHERE code=:c"), {"c": code})
