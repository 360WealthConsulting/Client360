"""Seed portal runtime gates.

Creates governed runtime metadata for the client portal while keeping
external access disabled by default.
"""

import sqlalchemy as sa
from alembic import op


revision = "9483fa25e622"
down_revision = "docdisp01"
branch_labels = None
depends_on = None


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


_ITEMS = (
    ("portal.mfa_required", "boolean", "true"),
    ("portal.production_signed_off", "boolean", "false"),
)


def upgrade():
    bind = op.get_bind()

    for code in _FLAGS:
        exists = bind.execute(
            sa.text(
                "SELECT id FROM configuration_feature_flags WHERE code=:c"
            ),
            {"c": code},
        ).scalar()

        if exists is None:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO configuration_feature_flags
                    (code, name, status, enabled, rollout_percentage)
                    VALUES
                    (:c, :c, 'active', false, 0)
                    """
                ),
                {"c": code},
            )

    set_id = bind.execute(
        sa.text(
            "SELECT id FROM configuration_sets WHERE code='runtime-defaults'"
        )
    ).scalar()

    if set_id is None:
        set_id = bind.execute(
            sa.text(
                """
                INSERT INTO configuration_sets
                (code, name, status)
                VALUES
                ('runtime-defaults', 'Runtime Defaults', 'active')
                RETURNING id
                """
            )
        ).scalar()

    for code, value_type, value in _ITEMS:
        exists = bind.execute(
            sa.text(
                "SELECT id FROM configuration_items WHERE code=:c"
            ),
            {"c": code},
        ).scalar()

        if exists is None:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO configuration_items
                    (set_id, code, name, value_type, value, status, version)
                    VALUES
                    (:s, :c, :c, :vt, CAST(:v AS json), 'active', 1)
                    """
                ),
                {
                    "s": set_id,
                    "c": code,
                    "vt": value_type,
                    "v": value,
                },
            )


def downgrade():
    bind = op.get_bind()

    for code in _FLAGS:
        bind.execute(
            sa.text(
                "DELETE FROM configuration_feature_flags WHERE code=:c"
            ),
            {"c": code},
        )

    for code, _, _ in _ITEMS:
        bind.execute(
            sa.text(
                "DELETE FROM configuration_items WHERE code=:c"
            ),
            {"c": code},
        )