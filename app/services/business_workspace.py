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
from app.services.document_platform.lifecycle import active_unarchived_clause
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


#: Documents per page on the organization profile.
#:
#: This is a PAGE SIZE, not a cap. Every document an organization owns is reachable by paging; the
#: previous ``limit(200)`` was a cap, and 16 production documents on two organizations sat past it
#: with no control that could reach them and nothing on the screen saying so. The heading has always
#: rendered the true total, so the profile showed "Documents (514)" above 200 rows — the count and
#: the list disagreed, and the count was the honest one.
DOCUMENTS_PER_PAGE = 100


def _document_page(c, business_id, page):
    """One page of an organization's documents, plus the paging facts the screen needs.

    ORDERING IS A TOTAL ORDER, deliberately. ``created_at DESC`` alone is not: TaxDome and Drake
    imports stamp many rows in the same second, and PostgreSQL may return tied rows in any order it
    likes, differently per query. Under a LIMIT/OFFSET that is not cosmetic — a row can appear on
    two pages while another appears on none. ``id DESC`` breaks every tie, so the sequence is stable
    across pages and across repeated reads.

    Isolation and lifecycle are unchanged and applied to BOTH the page and the count: the same
    ``organization_id`` equality and the same ``active_unarchived_clause`` that governed the capped
    read, so pagination widens reachability without widening what is visible.
    """
    where = (documents.c.organization_id == business_id, active_unarchived_clause())
    total = c.scalar(select(func.count()).select_from(documents).where(*where)) or 0

    page_count = max(1, -(-total // DOCUMENTS_PER_PAGE))     # ceiling division
    # Clamp rather than 404: a bookmarked deep link, or a page whose documents were archived since,
    # should land on the last real page instead of an empty screen that looks like data loss.
    page = max(1, min(int(page or 1), page_count))

    rows = c.execute(
        select(documents.c.id, documents.c.original_name, documents.c.display_name,
               documents.c.household_id, documents.c.person_id, documents.c.created_at)
        .where(*where)
        .order_by(documents.c.created_at.desc(), documents.c.id.desc())
        .limit(DOCUMENTS_PER_PAGE).offset((page - 1) * DOCUMENTS_PER_PAGE)
    ).mappings().all()

    return rows, {
        "document_count": total,
        "page": page,
        "per_page": DOCUMENTS_PER_PAGE,
        "page_count": page_count,
        "has_prev": page > 1,
        "has_next": page < page_count,
        "first_index": (page - 1) * DOCUMENTS_PER_PAGE + 1 if total else 0,
        "last_index": (page - 1) * DOCUMENTS_PER_PAGE + len(rows),
    }


def _document_household_ids(c, business_id):
    """Households referenced by ANY of this organization's documents, not just the current page.

    Related households are a property of the organization, so they must not change as a reader pages
    through the document list. The capped read happened to compute them from the rows it had; with
    paging that would have made the Related-households panel flicker between pages, which reads as
    the relationship graph changing under you.
    """
    return set(c.scalars(
        select(documents.c.household_id).where(
            documents.c.organization_id == business_id,
            documents.c.household_id.isnot(None),
            active_unarchived_clause()).distinct()))


def get_business_workspace(business_id: int, *, page: int = 1) -> dict | None:
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

        docs, paging = _document_page(c, business_id, page)
        related_household_ids |= _document_household_ids(c, business_id)

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
            # ``document_count`` is the organization's TRUE total and always has been — the heading
            # rendered it while the list showed at most 200 rows, so the two disagreed and only the
            # count was right. The rest of ``paging`` is what lets the list catch up with it.
            **paging,
            "provenance": sorted({o["evidence_source"] for o in owners if o["evidence_source"]}),
        }
