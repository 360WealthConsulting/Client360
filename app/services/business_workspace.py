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
    households,
    relationship_entities,
    relationship_ownership,
    relationship_types,
    relationships,
)

_ENTITY_KINDS = ("business", "trust", "estate", "organization")


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
                   relationship_ownership.c.ownership_percentage,
                   relationship_ownership.c.ownership_type, relationship_ownership.c.is_direct,
                   relationship_ownership.c.evidence_source)
            .select_from(relationships
                .join(relationship_types, relationship_types.c.id == relationships.c.relationship_type_id)
                .join(fe, fe.c.id == relationships.c.from_entity_id)
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
                "name": r["owner_name"], "person_id": r["person_id"], "household_id": r["household_id"],
                "workspace_url": nav, "ownership_percentage": r["ownership_percentage"],
                "ownership_type": r["ownership_type"], "is_direct": r["is_direct"],
                "evidence_source": r["evidence_source"],
                "is_owner": r["relationship_code"] in ("owns", "owner")})

        docs = c.execute(
            select(documents.c.id, documents.c.original_name, documents.c.household_id,
                   documents.c.person_id, documents.c.created_at)
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
                                        .where(households.c.id.in_(related_household_ids))):
                households_out.append({"household_id": hid, "name": hname,
                                       "workspace_url": f"/client/household/{hid}"})

        return {
            "id": ent["id"], "name": ent["name"], "entity_type": ent["entity_type"],
            "active": ent["active"],
            "owners": [o for o in owners if o["is_owner"]],
            "associated_people": [o for o in owners if not o["is_owner"] and o["person_id"]],
            "related_households": households_out,
            "documents": [{"id": d["id"], "name": d["original_name"],
                           "download_url": f"/documents/{d['id']}/download"} for d in docs],
            "document_count": doc_total,
            "provenance": sorted({o["evidence_source"] for o in owners if o["evidence_source"]}),
        }
