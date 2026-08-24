"""document display name (360Plus / Client360)

Adds ONE nullable column, ``documents.display_name``: the canonical, human-readable name Client360
shows staff for a document. It is a presentation field only.

Deliberately NOT a rename. ``original_name``, ``stored_name``, ``storage_path``, ``storage_uri`` and
``sha256`` are untouched and remain the authoritative provenance and the only way the physical file is
located; no file is renamed or moved, and nothing in SharePoint/OneDrive is affected. Reads fall back
to ``original_name`` whenever ``display_name`` is NULL or blank, so the column being empty is a normal
state, not a defect — most documents will never receive one.

Additive, nullable, no backfill, no index, fully reversible.
"""
import sqlalchemy as sa
from alembic import op

revision = "docdisp01"
down_revision = "payroll01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("display_name", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("documents", "display_name")
