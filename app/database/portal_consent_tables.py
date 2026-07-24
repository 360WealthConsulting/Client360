"""Declared schema for the Phase D.43 portal consent records.

Mirrors the live schema created by migration ``m4p5o6r7t8c9``. This is the ONLY new persistent structure
D.43 adds — a governed, versioned consent ledger for the existing Client Portal (portal terms, privacy
notice, electronic delivery, secure messaging, document delivery). It stores consent-management metadata
+ authoritative foreign references only — no duplicated client name/contact/financial/planning data.
"""
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB


def define_portal_consent_tables(metadata: MetaData):
    portal_consents = Table(
        "portal_consents", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("consent_uid", Text, nullable=False, unique=True),   # opaque external reference
        Column("portal_account_id", BigInteger,
               ForeignKey("portal_accounts.id", ondelete="CASCADE"), nullable=False),
        Column("consent_type", Text, nullable=False),               # portal_terms / privacy_notice / ...
        Column("version", Text, nullable=False),
        Column("state", Text, nullable=False),                      # accepted | declined | withdrawn
        Column("accepted_at", DateTime(timezone=True)),
        Column("withdrawn_at", DateTime(timezone=True)),
        Column("superseded_by", BigInteger, ForeignKey("portal_consents.id", ondelete="SET NULL")),
        # references-only request metadata (hashed ip / safe user-agent); NEVER raw tokens or content.
        Column("request_metadata", JSONB, nullable=False, server_default="{}"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("portal_account_id", "consent_type", "version",
                         name="uq_portal_consent_account_type_version"),
    )
    return {"portal_consents": portal_consents}
