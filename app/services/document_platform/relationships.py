"""Document relationships (Phase D.16) — polymorphic multi-domain links + consumer reads.

A document may relate to many entities (person/household/organization/opportunity/campaign/
referral_source/annual_review/business_owner_plan/compliance_review/advisor_work/timeline_event).
Relationships never own the document. ``documents_for_entity`` is the read-only visibility read
consumed by Annual Review, Business Owner Planning, Opportunity, Campaign, Referral, and
Compliance — they see documents, they do not own them.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, or_, select

from app.db import document_relationships, documents, engine, people
from app.services.document_platform.lifecycle import active_unarchived_clause

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
    caller has already established the entity is in scope.

    Excluded rows are ``lifecycle.active_unarchived_clause`` — BOTH delete markers and BOTH archive
    markers. This used to filter on ``status`` alone, which let a half-retired merge row
    (``deleted_at`` stamped, status untouched) surface in every consumer that reads through here;
    it then filtered soft-delete only, which let archived rows do the same."""
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
        # Soft-delete is marked by BOTH ``status='deleted'`` and ``deleted_at`` (document_platform
        # .service.soft_delete writes them together; restore clears both). Checking only ``status``
        # leaks rows where one marker was written without the other, so both are required here.
        # Archived rows are excluded on the same terms — see lifecycle.active_unarchived_clause.
        rows = c.execute(select(documents).where(and_(
            or_(*conds), active_unarchived_clause())).order_by(documents.c.id.desc())
            .limit(limit)).mappings().all()
    return [dict(r) for r in rows]


def client_documents(principal, entity_type: str, entity_id: int, *, limit=500) -> list[dict]:
    """Every ACTIVE document belonging to one client — the person+household union.

    "A client's documents" is not one column. A person's paperwork is anchored three ways: on the
    person, on the household they belong to (the joint return, the family trust deed), and through
    an explicit ``document_relationships`` link. ``documents_for_entity`` deliberately answers the
    narrower question its other consumers ask — "what is linked to THIS entity" — so asking it for a
    person returns only person-anchored rows. On the White household that is 4 documents out of 291:
    the joint return, every organizer and every statement is anchored on the household and silently
    disappears from both spouses' views.

    So this is the CLIENT-level read, and it is symmetric:
      person      -> that person + their household + every relationship link to either
      household   -> that household + all its member people + every relationship link to any

    Each row is tagged with ``anchor`` ("person" / "household" / "related") so the UI can say WHERE
    a document is filed instead of implying the person owns it. That distinction is the whole
    safety story here: this is a read-side union and writes nothing — no document is re-anchored,
    no owner is inferred, and a household document keeps reading as the household's.

    Organization entities have no such union and fall through to the single-entity read.
    """
    if entity_type not in ("person", "household"):
        return documents_for_entity(principal, entity_type, entity_id, limit=limit)

    with engine.connect() as c:
        person_ids, household_ids = _client_anchors(c, entity_type, entity_id)
        anchor_conds = []
        if person_ids:
            anchor_conds.append(documents.c.person_id.in_(tuple(person_ids)))
        if household_ids:
            anchor_conds.append(documents.c.household_id.in_(tuple(household_ids)))

        rel_conds = []
        if person_ids:
            rel_conds.append(and_(document_relationships.c.entity_type == "person",
                                  document_relationships.c.entity_id.in_(tuple(person_ids))))
        if household_ids:
            rel_conds.append(and_(document_relationships.c.entity_type == "household",
                                  document_relationships.c.entity_id.in_(tuple(household_ids))))
        if rel_conds:
            anchor_conds.append(documents.c.id.in_(
                select(document_relationships.c.document_id).where(or_(*rel_conds))))
        if not anchor_conds:
            return []

        rows = c.execute(select(documents).where(and_(
            or_(*anchor_conds), active_unarchived_clause()))
            .order_by(documents.c.id.desc()).limit(limit)).mappings().all()

    return [_tag_anchor(dict(r), person_ids, household_ids) for r in rows]


def _client_anchors(c, entity_type, entity_id):
    """The person ids and household ids that make up ONE client, for either entry point."""
    person_ids, household_ids = set(), set()
    if entity_type == "person":
        person_ids.add(int(entity_id))
        hh = c.scalar(select(people.c.household_id).where(people.c.id == int(entity_id)))
        if hh:
            household_ids.add(int(hh))
    elif entity_type == "household":
        household_ids.add(int(entity_id))
        person_ids |= {int(i) for i in c.scalars(
            select(people.c.id).where(people.c.household_id == int(entity_id)))}
    return person_ids, household_ids


def _tag_anchor(d, person_ids, household_ids):
    d["anchor"] = ("person" if d.get("person_id") in person_ids
                   else "household" if d.get("household_id") in household_ids
                   else "related")
    return d


def client_document(principal, entity_type: str, entity_id: int, document_id: int) -> dict | None:
    """ONE active document, but only if it belongs to this client — else None.

    The single-document form of :func:`client_documents`, and the membership test the preview
    drawer authorises on. It is a targeted query rather than a scan of the client's list precisely
    so that it stays exact: a list read is capped by ``limit``, and a client past that cap would
    otherwise have documents that exist in the table, render in the table, and 404 from the drawer.
    """
    if entity_type not in ("person", "household"):
        return next((d for d in documents_for_entity(principal, entity_type, entity_id, limit=1000)
                     if d["id"] == document_id), None)
    with engine.connect() as c:
        person_ids, household_ids = _client_anchors(c, entity_type, entity_id)
        conds = []
        if person_ids:
            conds.append(documents.c.person_id.in_(tuple(person_ids)))
        if household_ids:
            conds.append(documents.c.household_id.in_(tuple(household_ids)))
        rel = []
        if person_ids:
            rel.append(and_(document_relationships.c.entity_type == "person",
                            document_relationships.c.entity_id.in_(tuple(person_ids))))
        if household_ids:
            rel.append(and_(document_relationships.c.entity_type == "household",
                            document_relationships.c.entity_id.in_(tuple(household_ids))))
        if rel:
            conds.append(documents.c.id.in_(
                select(document_relationships.c.document_id).where(or_(*rel))))
        if not conds:
            return None
        row = c.execute(select(documents).where(and_(
            documents.c.id == document_id, or_(*conds),
            active_unarchived_clause()))).mappings().first()
    return _tag_anchor(dict(row), person_ids, household_ids) if row else None
