"""Declared schema for the Phase D.7 compliance review + decision ledger.

Mirrors the live schema created by migration ``e7c8o9m1p2q3`` so the declared and
migrated schemas stay consistent. The append-only trigger on ``compliance_decisions``
and the partial-unique guard on ``compliance_reviews`` live only in the migration
(behavioural constraints), not here.
"""
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

_REVIEW_STATUSES = (
    "pending_submission", "pending_assignment", "pending_review",
    "blocked_pending_authorized_reviewer", "approved", "approved_with_conditions",
    "returned", "declined", "superseded", "closed",
)
_DECISIONS = ("approved", "approved_with_conditions", "returned", "declined")


def define_compliance_tables(metadata: MetaData):
    compliance_reviews = Table(
        "compliance_reviews", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("recommendation_id", Text, nullable=False),
        Column("recommendation_type", Text, nullable=False),
        Column("source_entity_type", Text, nullable=False),
        Column("source_entity_id", BigInteger, nullable=False),
        Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("household_id", Integer, ForeignKey("households.id", ondelete="SET NULL")),
        Column("governing_rule", Text, nullable=False),
        Column("rule_version", Text, nullable=False),
        Column("policy_gate", Text, nullable=False),
        Column("recommendation_snapshot", JSONB, nullable=False),
        Column("evidence_snapshot", JSONB, nullable=False),
        Column("status", Text, nullable=False),
        Column("submitted_at", DateTime(timezone=True), nullable=False),
        Column("submitted_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("assigned_reviewer_role", Text),
        Column("assigned_reviewer_name", Text),
        Column("assigned_reviewer_principal_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint("status IN (" + ", ".join(f"'{s}'" for s in _REVIEW_STATUSES) + ")",
                        name="ck_compliance_reviews_status"),
    )
    compliance_decisions = Table(
        "compliance_decisions", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("compliance_review_id", BigInteger,
               ForeignKey("compliance_reviews.id", ondelete="RESTRICT"), nullable=False),
        Column("decision", Text, nullable=False),
        Column("reviewer_role", Text),
        Column("reviewer_name", Text),
        Column("reviewer_principal_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("decided_at", DateTime(timezone=True), nullable=False),
        Column("scope_reviewed", Text),
        Column("comments", Text),
        Column("exceptions", Text),
        Column("governing_rule", Text, nullable=False),
        Column("rule_version", Text, nullable=False),
        Column("evidence_snapshot", JSONB, nullable=False),
        Column("supersedes_decision_id", BigInteger,
               ForeignKey("compliance_decisions.id", ondelete="RESTRICT")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint("decision IN (" + ", ".join(f"'{d}'" for d in _DECISIONS) + ")",
                        name="ck_compliance_decisions_decision"),
    )
    reviewer_authorities = Table(
        "reviewer_authorities", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("principal_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("reviewer_role", Text, nullable=False),
        Column("reviewer_name", Text),
        Column("authority_scope", JSONB, nullable=False, server_default="[]"),
        Column("effective_date", Date),
        Column("expiration_date", Date),
        Column("status", Text, nullable=False, server_default="draft"),
        Column("source_reference", Text),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        # Phase D.8 administration fields.
        Column("evidence_description", Text),
        Column("recorded_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("recorded_at", DateTime(timezone=True), server_default=func.now()),
        Column("suspended_at", DateTime(timezone=True)),
        Column("revoked_at", DateTime(timezone=True)),
        Column("revocation_reason", Text),
        Column("supersedes_authority_id", BigInteger, ForeignKey("reviewer_authorities.id", ondelete="RESTRICT")),
        CheckConstraint("status IN ('draft', 'active', 'suspended', 'expired', 'revoked', 'superseded')",
                        name="ck_reviewer_authorities_status"),
    )
    reviewer_authority_events = Table(
        "reviewer_authority_events", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("reviewer_authority_id", BigInteger,
               ForeignKey("reviewer_authorities.id", ondelete="RESTRICT"), nullable=False),
        Column("event_type", Text, nullable=False),
        Column("prior_status", Text),
        Column("new_status", Text),
        Column("actor_principal_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("occurred_at", DateTime(timezone=True), nullable=False),
        Column("reason", Text),
        Column("evidence_snapshot", JSONB, nullable=False, server_default="{}"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    return {
        "compliance_reviews": compliance_reviews,
        "compliance_decisions": compliance_decisions,
        "reviewer_authorities": reviewer_authorities,
        "reviewer_authority_events": reviewer_authority_events,
    }
