"""Business workspace — "Related household(s)" derived from the canonical owner/person.

A person-backed owner entity carries ``person_id`` with ``household_id`` NULL (only a
household-backed entity sets ``household_id``), so reading the owner entity row alone can never
surface a person owner's household. Before this fix a business owned by a person rendered
"No related households" unless one of its documents happened to carry a household_id.

Household context is read THROUGH the owner to their existing membership. It is context, not
ownership: no household->business edge is created, no percentage is set, and a household member who
is not an owner (Julie Ann) never becomes one. Reading the workspace writes nothing.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, insert, select, update

from app.db import (
    engine,
    household_relationships,
    people,
    relationship_ownership,
    relationship_types,
    relationships,
)
from app.services.business_workspace import get_business_workspace
from app.services.relationships import ensure_person_entity
from tests.test_business_resolution import (
    _cleanup,
    _mk_business,
    _mk_doc,
    _mk_household,
    _mk_person,
)


def _tag():
    return "BWH" + uuid.uuid4().hex[:8]


def _own_edge(c, person_id, business_id):
    """An active ownership edge person -> business, exactly as record_ownership shapes it."""
    rt = c.execute(select(relationship_types.c.id).where(
        relationship_types.c.category.in_(("ownership", "org_structure")),
        relationship_types.c.code == "owns").limit(1)).scalar_one()
    owner_entity = ensure_person_entity(c, person_id)
    rel = c.execute(insert(relationships).values(
        from_entity_id=owner_entity, to_entity_id=business_id, relationship_type_id=rt,
        active=True, source="test").returning(relationships.c.id)).scalar_one()
    c.execute(insert(relationship_ownership).values(
        relationship_id=rel, is_direct=True,
        evidence_source="business_resolution:wealthbox.company_name+job_title"))
    return rel


def _graph_counts(business_id):
    """(relationship rows into the business, relationship_ownership rows for them)."""
    with engine.connect() as c:
        rel_ids = list(c.scalars(select(relationships.c.id)
                                 .where(relationships.c.to_entity_id == business_id)))
        detail = c.scalar(select(func.count()).select_from(relationship_ownership)
                          .where(relationship_ownership.c.relationship_id.in_(rel_ids or [-1]))) or 0
    return len(rel_ids), detail


def _hh_ids(ws):
    return sorted(h["household_id"] for h in ws["related_households"])


def test_owner_via_people_household_id_yields_related_household():
    """Membership represented ONLY as people.household_id (no household_relationships row)."""
    tag = _tag()
    try:
        with engine.begin() as c:
            hh = _mk_household(c, tag, "Legacy Column Household")
            pid = _mk_person(c, tag, "Ada", "Colwell", doc=True)      # no household_relationships row
            c.execute(update(people).where(people.c.id == pid).values(household_id=hh))
            biz = _mk_business(c, f"Colwell Systems LLC {tag}")
            _own_edge(c, pid, biz)
            assert c.scalar(select(func.count()).select_from(household_relationships)
                            .where(household_relationships.c.person_id == pid)) == 0
        ws = get_business_workspace(biz)
        assert _hh_ids(ws) == [hh]
        assert [o["person_id"] for o in ws["owners"]] == [pid]
    finally:
        _cleanup(tag)


def test_owner_via_household_relationships_yields_related_household():
    """Membership represented as a household_relationships member row (the canonical join the
    resolver itself reads) — this is the Pullen production shape."""
    tag = _tag()
    try:
        with engine.begin() as c:
            hh = _mk_household(c, tag, "Member Row Household")
            pid = _mk_person(c, tag, "Boyd", "Renfro", household_id=hh)
            c.execute(update(people).where(people.c.id == pid).values(household_id=None))
            biz = _mk_business(c, f"Renfro Excavating LLC {tag}")
            _own_edge(c, pid, biz)
            assert c.scalar(select(func.count()).select_from(household_relationships)
                            .where(household_relationships.c.person_id == pid)) == 1
        ws = get_business_workspace(biz)
        assert _hh_ids(ws) == [hh]
    finally:
        _cleanup(tag)


def test_two_owners_in_the_same_household_yield_one_related_household():
    tag = _tag()
    try:
        with engine.begin() as c:
            hh = _mk_household(c, tag, "Shared Household")
            a = _mk_person(c, tag, "Cora", "Vandel", household_id=hh)
            b = _mk_person(c, tag, "Dean", "Vandel", household_id=hh)
            biz = _mk_business(c, f"Vandel Brothers LLC {tag}")
            _own_edge(c, a, biz)
            _own_edge(c, b, biz)
        ws = get_business_workspace(biz)
        assert _hh_ids(ws) == [hh]                       # deduplicated, not listed twice
        assert len(ws["related_households"]) == 1
        assert sorted(o["person_id"] for o in ws["owners"]) == sorted([a, b])
    finally:
        _cleanup(tag)


def test_owner_without_a_household_yields_no_related_household():
    tag = _tag()
    try:
        with engine.begin() as c:
            pid = _mk_person(c, tag, "Elle", "Stack", doc=True)       # no household at all
            biz = _mk_business(c, f"Stack Consulting LLC {tag}")
            _own_edge(c, pid, biz)
        ws = get_business_workspace(biz)
        assert ws["related_households"] == []            # nothing invented
        assert [o["person_id"] for o in ws["owners"]] == [pid]
    finally:
        _cleanup(tag)


def test_pullen_shape_owner_drives_household_without_a_household_tagged_document():
    """The Pullen production shape: Norman owns the business and belongs to the household; Julie Ann
    is a household member with NO ownership edge; the business documents are organization-assigned
    with household_id NULL. The household must come from the OWNER, not from a document."""
    tag = _tag()
    try:
        with engine.begin() as c:
            hh = _mk_household(c, tag, "Norman & Julie Ann Pullen Household")
            norman = _mk_person(c, tag, "Norman", "Pullen", household_id=hh)
            julie = _mk_person(c, tag, "Julie Ann", "Pullen", household_id=hh)
            stale = _mk_person(c, tag, "Norm", "Pullen")             # stale duplicate, no edge
            biz = _mk_business(c, f"Pullen Homes Inc {tag}")
            _mk_doc(c, tag, organization_id=biz)                     # household_id NULL, as in prod
            _own_edge(c, norman, biz)

        before = _graph_counts(biz)
        ws = get_business_workspace(biz)

        assert [o["person_id"] for o in ws["owners"]] == [norman]
        assert ws["owners"][0]["workspace_url"] == f"/client/{norman}"
        assert ws["owners"][0]["ownership_percentage"] is None
        assert _hh_ids(ws) == [hh]
        assert ws["related_households"][0]["workspace_url"] == f"/client/household/{hh}"
        assert ws["related_households"][0]["name"] == f"{tag} Norman & Julie Ann Pullen Household"

        # Julie Ann is a household member ONLY — never an owner or an associated business person.
        assert julie not in {o["person_id"] for o in ws["owners"]}
        assert julie not in {a["person_id"] for a in ws["associated_people"]}
        # The stale duplicate never appears either.
        assert stale not in {o["person_id"] for o in ws["owners"]}
        assert ws["document_count"] == 1                             # documents still listed

        # The read created NO relationship or ownership rows to make the UI render.
        assert _graph_counts(biz) == before == (1, 1)
    finally:
        _cleanup(tag)


def test_reading_the_workspace_writes_nothing():
    tag = _tag()
    try:
        with engine.begin() as c:
            hh = _mk_household(c, tag, "Idempotent Read Household")
            pid = _mk_person(c, tag, "Frank", "Oyler", household_id=hh)
            biz = _mk_business(c, f"Oyler Fabrication LLC {tag}")
            _own_edge(c, pid, biz)

        before = _graph_counts(biz)
        hr_before = _hh_membership(pid)
        for _ in range(3):
            ws = get_business_workspace(biz)
            assert _hh_ids(ws) == [hh]
        assert _graph_counts(biz) == before
        assert _hh_membership(pid) == hr_before          # membership never mutated by a read
    finally:
        _cleanup(tag)


def _hh_membership(person_id):
    with engine.connect() as c:
        rows = sorted(c.execute(
            select(household_relationships.c.household_id,
                   household_relationships.c.relationship_type,
                   household_relationships.c.is_primary)
            .where(household_relationships.c.person_id == person_id)).all())
        col = c.scalar(select(people.c.household_id).where(people.c.id == person_id))
    return rows, col


def test_person_workspace_still_shows_the_business():
    """Reverse navigation (client360 relationships section) is unchanged by this read-model fix."""
    tag = _tag()
    try:
        with engine.begin() as c:
            hh = _mk_household(c, tag, "Reverse Nav Household")
            pid = _mk_person(c, tag, "Greta", "Halloran", household_id=hh)
            biz = _mk_business(c, f"Halloran Design LLC {tag}")
            _own_edge(c, pid, biz)
        from app.services.organization_service import list_person_business_ownership
        assert biz in {b["business_id"] for b in list_person_business_ownership(pid)}
        ws = get_business_workspace(biz)
        assert ws["owners"][0]["workspace_url"] == f"/client/{pid}"   # owner navigation preserved
        assert _hh_ids(ws) == [hh]
    finally:
        _cleanup(tag)
