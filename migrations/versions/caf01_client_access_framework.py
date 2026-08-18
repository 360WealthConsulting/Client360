"""Client Feature & Access Control framework.

Four thin, extensible tables (row-per-entity — no per-feature boolean columns) backing the 360Plus
access-control framework. The feature/product CATALOG is code (app/services/features/catalog.py), so new
capabilities need no migration; only these control rows are persisted:

  * client_product_entitlements — which products (wealth/business) a subject holds (core is baseline).
  * client_feature_overrides    — per-subject feature override (enable/disable; inherit = no row).
  * firm_feature_controls       — firm-wide feature state (enabled/disabled/beta/internal_only).
  * client_status               — subject lifecycle status + resolved disposition.

Additive and reversible. No existing table, capability, or ownership is modified. Reuses existing
capabilities (client.read/write + record scope; configuration.view/admin) — none are seeded here.
"""
import sqlalchemy as sa
from alembic import op

revision = "caf01"
down_revision = "spdelta01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "client_product_entitlements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subject_type", sa.String(20), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("product", sa.String(30), nullable=False),
        sa.Column("granted_by_user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("subject_type", "subject_id", "product",
                            name="uq_client_product_entitlement"),
    )
    op.create_index("ix_client_product_entitlements_subject", "client_product_entitlements",
                    ["subject_type", "subject_id"])

    op.create_table(
        "client_feature_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subject_type", sa.String(20), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("feature_key", sa.String(60), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),           # enable | disable
        sa.Column("updated_by_user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("subject_type", "subject_id", "feature_key",
                            name="uq_client_feature_override"),
    )
    op.create_index("ix_client_feature_overrides_subject", "client_feature_overrides",
                    ["subject_type", "subject_id"])

    op.create_table(
        "firm_feature_controls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("feature_key", sa.String(60), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),           # enabled | disabled | beta | internal_only
        sa.Column("updated_by_user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("feature_key", name="uq_firm_feature_control"),
    )

    op.create_table(
        "client_status",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subject_type", sa.String(20), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),          # active | inactive | needs_review
        sa.Column("disposition", sa.String(30)),                     # active|inactive|prospect|archive (nullable)
        sa.Column("updated_by_user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("subject_type", "subject_id", name="uq_client_status_subject"),
    )


def downgrade():
    op.drop_table("client_status")
    op.drop_table("firm_feature_controls")
    op.drop_index("ix_client_feature_overrides_subject", table_name="client_feature_overrides")
    op.drop_table("client_feature_overrides")
    op.drop_index("ix_client_product_entitlements_subject", table_name="client_product_entitlements")
    op.drop_table("client_product_entitlements")
