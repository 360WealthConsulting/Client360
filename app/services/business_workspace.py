"""Read-only Business Workspace composition (D.12 relationship graph reuse).

Assembles a business/organization entity view from the AUTHORITATIVE ownership graph
(``relationships`` category ownership/org_structure + ``relationship_ownership``) and the
existing document ownership (``documents.organization_id``). Pure read; no writes, no ``ensure_*``
side effects. The caller enforces the ``client.read`` capability.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.db import (
    documents,
    engine,
    household_relationships,
    households,
    people,
    relationship_entities,
    relationship_ownership,
    relationship_types,
    relationships,
)
from app.services.document_naming import document_display_name
from app.services.person_names import person_display_name

_ENTITY_KINDS = ("business", "trust", "estate", "organization")


def _display_name(entity_name, full_name, first, last):
    """Prefer the canonical person name; fall back to first+last, then to the entity's own name
    (which may be a placeholder such as 'Person 7783'). Never mutates stored names. Delegates to the
    canonical helper so there is one name resolution across the app."""
    return person_display_name(full_name, first, last, fallback=entity_name)


def _person_household_ids(connection, person_ids):
    """Households the given people belong to, from the canonical membership model.

    Household membership is written two ways by the canonical services (``app.services.households``
    and ``household_derivation`` both set ``people.household_id`` AND insert a
    ``household_relationships`` member row), so both are read here — a related person contributes
    household context regardless of which representation exists on the row. Set-based and pure read:
    no per-person query, no ``ensure_*`` side effect, nothing written.
    """
    ids = [p for p in person_ids if p]
    if not ids:
        return set()
    found = set(connection.scalars(
        select(household_relationships.c.household_id).where(
            household_relationships.c.person_id.in_(ids),
            household_relationships.c.household_id.isnot(None))))
    found |= set(connection.scalars(
        select(people.c.household_id).where(
            people.c.id.in_(ids), people.c.household_id.isnot(None))))
    return found


def get_business_workspace(business_id: int) -> dict | None:
    fe = relationship_entities.alias("owner_entity")
    with engine.connect() as c:
        ent = c.execute(
            select(relationship_entities.c.id, relationship_entities.c.name,
                   relationship_entities.c.entity_type, relationship_entities.c.active)
            .where(relationship_entities.c.id == business_id,
                   relationship_entities.c.entity_type.in_(_ENTITY_KINDS))
        ).mappings().one_or_none()
        if ent is None:
            return None

        owner_rows = c.execute(
            select(relationships.c.id.label("relationship_id"),
                   relationship_types.c.code.label("relationship_code"),
                   relationship_types.c.name.label("relationship_label"),
                   fe.c.entity_type.label("owner_entity_type"), fe.c.name.label("owner_name"),
                   fe.c.person_id, fe.c.household_id,
                   people.c.full_name, people.c.first_name, people.c.last_name,
                   relationship_ownership.c.ownership_percentage,
                   relationship_ownership.c.ownership_type, relationship_ownership.c.is_direct,
                   relationship_ownership.c.evidence_source)
            .select_from(relationships
                .join(relationship_types, relationship_types.c.id == relationships.c.relationship_type_id)
                .join(fe, fe.c.id == relationships.c.from_entity_id)
                .outerjoin(people, people.c.id == fe.c.person_id)
                .outerjoin(relationship_ownership,
                           relationship_ownership.c.relationship_id == relationships.c.id))
            .where(relationships.c.to_entity_id == business_id,
                   relationships.c.active.is_(True),
                   relationship_types.c.category.in_(("ownership", "org_structure")))
        ).mappings().all()

        owners = []
        related_household_ids = set()
        for r in owner_rows:
            nav = (f"/client/{r['person_id']}" if r["person_id"]
                   else (f"/client/household/{r['household_id']}" if r["household_id"] else None))
            if r["household_id"]:
                related_household_ids.add(r["household_id"])
            owners.append({
                "relationship_id": r["relationship_id"], "code": r["relationship_code"],
                "role": r["relationship_label"], "entity_type": r["owner_entity_type"],
                "name": _display_name(r["owner_name"], r["full_name"], r["first_name"], r["last_name"]),
                "person_id": r["person_id"], "household_id": r["household_id"],
                "workspace_url": nav, "ownership_percentage": r["ownership_percentage"],
                "ownership_type": r["ownership_type"], "is_direct": r["is_direct"],
                "evidence_source": r["evidence_source"],
                "is_owner": r["relationship_code"] in ("owns", "owner")})

        # Related household CONTEXT — NOT ownership. A person-backed owner entity carries
        # person_id with household_id NULL (only a household-backed entity sets household_id), so
        # reading the entity row alone can never surface the owner's household; that is why a
        # business owned by a person showed "No related households". Read through the canonical
        # owner/person to their existing household membership instead. This confers no household
        # ownership, creates no household->business edge, and writes nothing; the set dedupes
        # households shared by several owners.
        related_household_ids |= _person_household_ids(
            c, {o["person_id"] for o in owners if o["person_id"]})

        docs = c.execute(
            select(documents.c.id, documents.c.original_name, documents.c.display_name,
                   documents.c.household_id, documents.c.person_id, documents.c.created_at)
            .where(documents.c.organization_id == business_id, documents.c.status != "deleted")
            .order_by(documents.c.created_at.desc()).limit(200)
        ).mappings().all()
        doc_total = c.scalar(select(func.count()).select_from(documents)
                             .where(documents.c.organization_id == business_id,
                                    documents.c.status != "deleted")) or 0
        for d in docs:
            if d["household_id"]:
                related_household_ids.add(d["household_id"])

        households_out = []
        if related_household_ids:
            for hid, hname in c.execute(select(households.c.id, households.c.name)
                                        .where(households.c.id.in_(related_household_ids))
                                        .order_by(households.c.name, households.c.id)):
                households_out.append({"household_id": hid, "name": hname,
                                       "workspace_url": f"/client/household/{hid}"})

        return {
            "id": ent["id"], "name": ent["name"], "entity_type": ent["entity_type"],
            "active": ent["active"],
            "owners": [o for o in owners if o["is_owner"]],
            "associated_people": [o for o in owners if not o["is_owner"] and o["person_id"]],
            "related_households": households_out,
            # source_kind is stated rather than assumed: these rows come only from
            # documents.organization_id, so they are canonical today, and saying so keeps the
            # template's canonical-only Email gate correct if a Vault merge is ever added here.
            "documents": [{"id": d["id"], "name": document_display_name(d),
                           "original_name": d["original_name"], "source_kind": "canonical",
                           "download_url": f"/documents/{d['id']}/download"} for d in docs],
            "document_count": doc_total,
            "provenance": sorted({o["evidence_source"] for o in owners if o["evidence_source"]}),
        }
