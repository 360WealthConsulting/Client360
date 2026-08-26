"""Make the portal sign-off and MFA gates genuine runtime feature flags.

``app/portal/gate.py::gate()`` evaluates EVERY entry in ``GATES`` through
``consumption.feature_enabled``. Migration ``9483fa25e622`` seeded
``portal.production_signed_off`` and ``portal.mfa_required`` as configuration ITEMS instead of feature
flags, so ``ctx.feature_defined(...)`` was False for both and ``feature_enabled`` silently returned the
hard-coded ``GATES`` default. The consequence was that neither gate was actually governed: writing the
configuration item changed nothing, ``portal.production_signed_off`` could never become True (so
``production_ready()`` could never be True), and ``portal.mfa_required`` reported True only because its
default is True.

Seed the two missing feature rows so the metadata conforms to the evaluation contract. Behaviour-
preserving by construction: the seeded values equal the effective values they replace —
``portal.production_signed_off`` active/disabled/rollout 0 (evaluates False) and ``portal.mfa_required``
active/enabled/rollout 100 (evaluates True). ``gate()`` is NOT changed.

The pre-existing ``configuration_items`` rows for both codes are deliberately LEFT IN PLACE for
compatibility and history; they are no longer consulted by ``gate()``. From this revision onward the
FEATURE-FLAG rows are authoritative for gate evaluation, and the governed write path for both codes is
``features.set_flag_status`` + ``features.update_flag_rollout`` + ``runtime_engine.refresh``.

Additive, idempotent (every insert guarded by a code lookup) and reversible: downgrade removes only these
two feature rows and leaves the configuration items untouched. Single Alembic head (down ``a7c31f9b4e02``).
"""
import sqlalchemy as sa
from alembic import op

revision = "b5d82e04c917"
down_revision = "a7c31f9b4e02"
branch_labels = None
depends_on = None

# (code, enabled, rollout_percentage) — each equals the gate's current EFFECTIVE value.
_FLAGS = (
    ("portal.production_signed_off", False, 0),    # compliance sign-off stays BLOCKED
    ("portal.mfa_required", True, 100),            # MFA stays REQUIRED
)


def upgrade():
    bind = op.get_bind()
    for code, enabled, rollout in _FLAGS:
        if bind.execute(sa.text("SELECT id FROM configuration_feature_flags WHERE code=:c"),
                        {"c": code}).scalar() is None:
            bind.execute(sa.text(
                "INSERT INTO configuration_feature_flags "
                "(code, name, status, enabled, rollout_percentage) "
                "VALUES (:c, :c, 'active', :e, :r)"),
                {"c": code, "e": enabled, "r": rollout})


def downgrade():
    bind = op.get_bind()
    # Remove ONLY the two feature rows. The historical configuration_items rows are left alone.
    for code, _enabled, _rollout in _FLAGS:
        bind.execute(sa.text("DELETE FROM configuration_feature_flags WHERE code=:c"), {"c": code})
