"""Seed the controlled-test local identity-provider gate.

``portal.production_signed_off`` removes the deterministic local identity provider, which is the only
portal identity provider that exists. Recording sign-off therefore made even a SYNTHETIC production test
impossible: both portal auth surfaces resolve a provider and would return 400 with none registered.

Seed one governed flag, ``portal.local_identity_provider_enabled``, that authorizes the local provider
independently of sign-off. Production-safe default OFF (status active, enabled false, rollout 0), so this
migration changes no behaviour on its own: registration stays exactly as it was until someone explicitly
enables the flag through the governed configuration surface.

This gate is for CONTROLLED SYNTHETIC TESTING ONLY. It does not affect ``production_ready()``, opens no
portal surface, and is NOT a substitute for the real external identity provider that
docs/CLIENT_PORTAL_COMPLIANCE_GATE.md still requires before any real client is onboarded.

Seeds ONLY this flag: portal.enabled, portal.production_signed_off, portal.mfa_required, the other portal
features and every configuration set/item are untouched. Idempotent and reversible. Single Alembic head
(down ``9483fa25e622``).
"""
import sqlalchemy as sa
from alembic import op

revision = "a7c31f9b4e02"
down_revision = "9483fa25e622"
branch_labels = None
depends_on = None

_FLAG = "portal.local_identity_provider_enabled"


def upgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT id FROM configuration_feature_flags WHERE code=:c"),
                    {"c": _FLAG}).scalar() is None:
        bind.execute(sa.text(
            "INSERT INTO configuration_feature_flags (code, name, status, enabled, rollout_percentage) "
            "VALUES (:c, :c, 'active', false, 0)"), {"c": _FLAG})


def downgrade():
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM configuration_feature_flags WHERE code=:c"), {"c": _FLAG})
