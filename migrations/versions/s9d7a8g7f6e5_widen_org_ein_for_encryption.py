"""widen organization_profiles.ein for encryption-at-rest (Release 0.9.11, Phase 2)

Justified schema correction. Phase 1 created ``organization_profiles.ein`` as
``VARCHAR(64)``, but ADR-18 §2.3 requires the EIN to be **encrypted at rest** (Fernet).
Fernet ciphertext of a ~10-character EIN is ~120 characters, which does not fit in 64 —
so the sensitive-field encryption implemented in Phase 2 cannot persist an EIN. Widen the
column to ``TEXT`` (encrypted values are opaque and variable-length).

Additive and reversible; single Alembic head preserved. Downgrade nulls any ciphertext
that would not fit back into ``VARCHAR(64)`` (encrypted values are meaningless once the
column narrows) before restoring the original type.
"""
from alembic import op
import sqlalchemy as sa

revision = "s9d7a8g7f6e5"
down_revision = "r8c69f7e6d5c"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("organization_profiles", "ein", type_=sa.Text(),
                    existing_type=sa.String(64), existing_nullable=True)


def downgrade():
    op.execute("UPDATE organization_profiles SET ein = NULL WHERE ein IS NOT NULL AND length(ein) > 64")
    op.alter_column("organization_profiles", "ein", type_=sa.String(64),
                    existing_type=sa.Text(), existing_nullable=True)
