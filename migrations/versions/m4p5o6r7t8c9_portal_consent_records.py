"""Client Portal consent records (Phase D.43).

Adds a single governed, versioned consent ledger (``portal_consents``) for the existing Client Portal —
portal terms, privacy notice, electronic delivery, secure messaging, and document delivery. It stores
consent-management metadata + authoritative foreign references only (no duplicated client data). This is
the ONLY new persistent structure D.43 adds; the portal is otherwise extended in code (visibility
registry, gates, diagnostics, governance) over the existing ``portal_*`` schema.

No new capability is seeded — the portal uses grant-based authorization (``portal_access_grants``), not
RBAC capabilities. Additive and reversible. Single Alembic head (down ``l3q4v5w6x7y8``).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "m4p5o6r7t8c9"
down_revision = "l3q4v5w6x7y8"
branch_labels = None
depends_on = None

_STATES = ("accepted", "declined", "withdrawn")
_TYPES = ("portal_terms", "privacy_notice", "electronic_delivery", "secure_messaging", "document_delivery")


def upgrade():
    op.create_table(
        "portal_consents",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("consent_uid", sa.Text, nullable=False, unique=True),
        sa.Column("portal_account_id", sa.BigInteger,
                  sa.ForeignKey("portal_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("consent_type", sa.Text, nullable=False),
        sa.Column("version", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True)),
        sa.Column("superseded_by", sa.BigInteger,
                  sa.ForeignKey("portal_consents.id", ondelete="SET NULL")),
        sa.Column("request_metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("portal_account_id", "consent_type", "version",
                            name="uq_portal_consent_account_type_version"),
        sa.CheckConstraint("state IN (" + ", ".join(f"'{s}'" for s in _STATES) + ")",
                           name="ck_portal_consent_state"),
        sa.CheckConstraint("consent_type IN (" + ", ".join(f"'{t}'" for t in _TYPES) + ")",
                           name="ck_portal_consent_type"),
    )
    op.create_index("ix_portal_consents_account", "portal_consents", ["portal_account_id"])


def downgrade():
    op.drop_table("portal_consents")
