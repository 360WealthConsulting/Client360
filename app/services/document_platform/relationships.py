"""Document relationships (Phase D.16) — polymorphic multi-domain links + consumer reads.

A document may relate to many entities (person/household/organization/opportunity/campaign/
referral_source/annual_review/business_owner_plan/compliance_review/advisor_work/timeline_event).
Relationships never own the document. ``documents_for_entity`` is the read-only visibility read
consumed by Annual Review, Business Owner Planning, Opportunity, Campaign, Referral, and
Compliance — they see documents, they do not own them.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, select

from app.db import document_relationships, documents, engine

ENTITY_TYPES = frozenset({"person", "household", "organization", "opportunity", "campaign",
                          "referral_source", "annual_review", "business_owner_plan",
                          "compliance_review", "advisor_work", "timeline_event"})


class RelationshipError(Exception):
    """Invalid relationship."""


def _now():
    return datetime.now(UTC)


def link_entity(principal, document_id: int, *, entity_type: str, entity_id: int, actor_user_id,
                relationship_type=None) -> dict:
    if entity_type not in ENTITY_TYPES:
        raise RelationshipError(f"unknown entity_type {entity_type!r}")
    with engine.begin() as c:
        if c.scalar(select(documents.c.id).where(documents.c.id == document_id)) is None:
            raise RelationshipError("document does not exist")
        existing = c.scalar(select(document_relationships.c.id).where(and_(
            document_relationships.c.document_id == document_id,
            document_relationships.c.entity_type == entity_type,
            document_relationships.c.entity_id == entity_id)))
        if existing:
            return {"id": existing}
        row = c.execute(document_relationships.insert().values(
            document_id=document_id, entity_type=entity_type, entity_id=entity_id,
            relationship_type=relationship_type, created_by=actor_user_id, created_at=_now())
            .returning(document_relationships)).mappings().one()
    return dict(row)


def unlink_entity(principal, document_id: int, *, entity_type: str, entity_id: int) -> None:
    with engine.begin() as c:
        c.execute(document_relationships.delete().where(and_(
            document_relationships.c.document_id == document_id,
            document_relationships.c.entity_type == entity_type,
            document_relationships.c.entity_id == entity_id)))


def list_relationships(document_id: int) -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(document_relationships)
                                           .where(document_relationships.c.document_id == document_id)).mappings()]


def documents_for_entity(principal, entity_type: str, entity_id: int, *, limit=100) -> list[dict]:
    """Read-only: documents related to an entity (via a document_relationship OR the document's
    own anchor columns), excluding soft-deleted. Consumers get visibility, never ownership. The
    caller has already established the entity is in scope."""
    with engine.connect() as c:
        related_ids = set(c.scalars(select(document_relationships.c.document_id).where(
            document_relationships.c.entity_type == entity_type,
            document_relationships.c.entity_id == entity_id)))
        conds = [documents.c.id.in_(tuple(related_ids))] if related_ids else []
        if entity_type == "person":
            conds.append(documents.c.person_id == entity_id)
        elif entity_type == "household":
            conds.append(documents.c.household_id == entity_id)
        elif entity_type == "organization":
            conds.append(documents.c.organization_id == entity_id)
        if not conds:
            return []
        from sqlalchemy import or_
        rows = c.execute(select(documents).where(and_(
            or_(*conds), documents.c.status != "deleted")).order_by(documents.c.id.desc())
            .limit(limit)).mappings().all()
    return [dict(r) for r in rows]
