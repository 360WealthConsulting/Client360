"""insurance product-version evolution: carrier codes + rider compatibility (Release 0.10.0, Phase 1)

Makes long-term carrier product evolution first-class (Phase 1 refinement to AD-5):
adds carrier_product_code and illustration_identifier to insurance_product_versions
(integration-matching keys, previously only expressible via the untyped spec JSON), and
a structured insurance_product_rider_compatibility table so which riders may attach to a
product version is queryable and validatable rather than a JSON list.

The spec JSON is retained for genuinely unstructured carrier-specific attributes only.
Additive and reversible; single Alembic head.
"""
import sqlalchemy as sa
from alembic import op

revision = "w3c4e5g6b7d8"
down_revision = "v2b3d4f5a6c7"
branch_labels = None
depends_on = None

RIDER_REQUIREMENTS = ("included", "available", "optional", "excluded")


def _check(col, allowed):
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in allowed) + ")"


def upgrade():
    # First-class integration-matching keys on the product version.
    op.add_column("insurance_product_versions", sa.Column("carrier_product_code", sa.String(64)))
    op.add_column("insurance_product_versions", sa.Column("illustration_identifier", sa.String(128)))
    # Indexed for lookup/matching. Not globally unique: different carriers legitimately reuse
    # codes, and versions of one product family share the same carrier product code — matching
    # is scoped by carrier at query time. Uniqueness lives at (family_id, version_label).
    op.create_index("ix_ins_pv_carrier_product_code", "insurance_product_versions", ["carrier_product_code"])
    op.create_index("ix_ins_pv_illustration_identifier", "insurance_product_versions", ["illustration_identifier"])

    op.create_table(
        "insurance_product_rider_compatibility",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("product_version_id", sa.Integer,
                  sa.ForeignKey("insurance_product_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rider_type", sa.String(48), nullable=False),
        sa.Column("requirement", sa.String(16), nullable=False, server_default="available"),
        sa.Column("carrier_rider_code", sa.String(64)),
        sa.Column("effective_from", sa.Date),
        sa.Column("effective_to", sa.Date),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_check("requirement", RIDER_REQUIREMENTS), name="ck_ins_rider_compat_requirement"),
        # a version lists each rider type once (the row's requirement is the answer)
        sa.UniqueConstraint("product_version_id", "rider_type", name="uq_ins_rider_compat"),
    )
    op.create_index("ix_ins_rider_compat_version", "insurance_product_rider_compatibility", ["product_version_id"])
    op.create_index("ix_ins_rider_compat_carrier_code", "insurance_product_rider_compatibility", ["carrier_rider_code"])


def downgrade():
    op.drop_table("insurance_product_rider_compatibility")
    op.drop_index("ix_ins_pv_illustration_identifier", table_name="insurance_product_versions")
    op.drop_index("ix_ins_pv_carrier_product_code", table_name="insurance_product_versions")
    op.drop_column("insurance_product_versions", "illustration_identifier")
    op.drop_column("insurance_product_versions", "carrier_product_code")
