"""Business Relationship Resolution — hardened PREVIEW resolver, role taxonomy, six-bucket safety,
duplicate-business detection, stale-person review, and name-display fallback.

Pullen Homes is the mandatory regression. Titles like President/CEO must NOT confer ownership; explicit
ownership tokens (including compound titles like 'Owner-Construction-Handyman') must; a stale owner with
no canonical goes to PERSON_IDENTITY_REVIEW; normalized duplicate businesses go to
DUPLICATE_BUSINESS_REVIEW. Fixtures are TAG-unique and fully torn down (no cross-test leakage).
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, insert, select

from app.db import (
    documents,
    engine,
    household_relationships,
    households,
    people,
    person_source_links,
    relationship_entities,
    relationship_ownership,
    relationship_types,
    relationships,
    source_contacts,
)
from app.services.business_resolution import (
    _classify_role,
    _display_name,
    resolve_business_relationships,
)
from app.services.business_workspace import get_business_workspace
from app.services.relationships import ensure_person_entity

_SENT = object()


# --------------------------------------------------------------------------- builders
def _mk_household(c, tag, name="Household"):
    return c.execute(insert(households).values(name=f"{tag} {name}").returning(households.c.id)).scalar_one()


def _mk_doc(c, tag, *, organization_id=None, household_id=None, person_id=None):
    u = uuid.uuid4().hex
    return c.execute(insert(documents).values(
        original_name=f"{tag} doc.pdf", stored_name=f"brz:{u}", storage_path=f"/x/{u}",
        size_bytes=1, sha256=u.ljust(64, "0")[:64], status="active",
        organization_id=organization_id, household_id=household_id, person_id=person_id,
    ).returning(documents.c.id)).scalar_one()


def _mk_person(c, tag, first, last, *, household_id=None, doc=False, full_name=_SENT):
    fn = f"{first} {last}{tag}" if full_name is _SENT else full_name
    pid = c.execute(insert(people).values(
        first_name=first, last_name=f"{last}{tag}", full_name=fn, household_id=household_id, active=True
    ).returning(people.c.id)).scalar_one()
    if household_id:
        c.execute(insert(household_relationships).values(
            household_id=household_id, person_id=pid, relationship_type="member",
            is_primary=False, is_primary_household=True))
    if doc:
        _mk_doc(c, tag, person_id=pid)
    return pid


def _mk_business(c, name):
    return c.execute(insert(relationship_entities).values(
        entity_type="business", name=name, active=True).returning(relationship_entities.c.id)).scalar_one()


def _mk_wb(c, tag, person_id, first, last, company_name, job_title):
    sc = c.execute(insert(source_contacts).values(
        source_system="Wealthbox", source_file="wb", source_hash=uuid.uuid4().hex,
        first_name=first, last_name=f"{last}{tag}", full_name=f"{first} {last}{tag}",
        raw_data={"company_name": company_name, "job_title": job_title}).returning(source_contacts.c.id)).scalar_one()
    if person_id is not None:
        c.execute(insert(person_source_links).values(
            person_id=person_id, source_contact_id=sc, match_method="auto_promote", confirmed=False))
    return sc


def _cleanup(tag):
    like = f"%{tag}%"
    with engine.begin() as c:
        ppl = list(c.scalars(select(people.c.id).where(
            people.c.last_name.like(like) | people.c.full_name.like(like) | people.c.first_name.like(like))))
        hhs = list(c.scalars(select(households.c.id).where(households.c.name.like(like))))
        scs = list(c.scalars(select(source_contacts.c.id).where(source_contacts.c.full_name.like(like))))
        ents = list(c.scalars(select(relationship_entities.c.id).where(
            relationship_entities.c.name.like(like))))
        if ppl:
            ents += list(c.scalars(select(relationship_entities.c.id).where(
                relationship_entities.c.person_id.in_(ppl))))
        if hhs:
            ents += list(c.scalars(select(relationship_entities.c.id).where(
                relationship_entities.c.household_id.in_(hhs))))
        ents = list(set(ents))
        if ents:
            rels = list(c.scalars(select(relationships.c.id).where(
                relationships.c.from_entity_id.in_(ents) | relationships.c.to_entity_id.in_(ents))))
            if rels:
                c.execute(delete(relationship_ownership).where(relationship_ownership.c.relationship_id.in_(rels)))
                c.execute(delete(relationships).where(relationships.c.id.in_(rels)))
            c.execute(delete(documents).where(documents.c.organization_id.in_(ents)))
            c.execute(delete(relationship_entities).where(relationship_entities.c.id.in_(ents)))
        if ppl or scs:
            c.execute(delete(person_source_links).where(
                person_source_links.c.person_id.in_(ppl or [-1])
                | person_source_links.c.source_contact_id.in_(scs or [-1])))
        if scs:
            c.execute(delete(source_contacts).where(source_contacts.c.id.in_(scs)))
        if hhs:
            c.execute(delete(household_relationships).where(household_relationships.c.household_id.in_(hhs)))
            c.execute(delete(documents).where(documents.c.household_id.in_(hhs)))
        if ppl:
            c.execute(delete(household_relationships).where(household_relationships.c.person_id.in_(ppl)))
            c.execute(delete(documents).where(documents.c.person_id.in_(ppl)))
            c.execute(delete(people).where(people.c.id.in_(ppl)))
        if hhs:
            c.execute(delete(households).where(households.c.id.in_(hhs)))


# --------------------------------------------------------------------------- unit: role + name
def test_role_classification_taxonomy():
    assert _classify_role("Owner", "X LLC") == "EXPLICIT_OWNERSHIP"
    assert _classify_role("Co-Owner", "X Inc") == "EXPLICIT_OWNERSHIP"
    assert _classify_role("Owner-Construction-Handyman", "Crossbow Services LLC") == "EXPLICIT_OWNERSHIP"
    assert _classify_role("Shareholder", "X Inc") == "EXPLICIT_OWNERSHIP"
    # Partner is ownership ONLY as a partner-role title; an arbitrary title containing "partner" is not.
    assert _classify_role("Partner", "X") == "EXPLICIT_OWNERSHIP"
    assert _classify_role("General Partner", "X LP") == "EXPLICIT_OWNERSHIP"
    assert _classify_role("Managing Partner", "X LP") == "EXPLICIT_OWNERSHIP"
    assert _classify_role("Sales Partner", "X") == "OFFICER_OR_MANAGEMENT_ONLY"
    assert _classify_role("Channel Partner", "X") == "OFFICER_OR_MANAGEMENT_ONLY"
    assert _classify_role("Member", "Foo LLC") == "EXPLICIT_OWNERSHIP"       # member + LLC → ownership
    assert _classify_role("Member", "Foo Inc") == "OFFICER_OR_MANAGEMENT_ONLY"  # member w/o LLC → not owner
    # Founder alone is NOT current ownership → association evidence only.
    assert _classify_role("Founder", "X Inc") == "OFFICER_OR_MANAGEMENT_ONLY"
    assert _classify_role("President", "X Inc") == "OFFICER_OR_MANAGEMENT_ONLY"
    assert _classify_role("CEO", "X Inc") == "OFFICER_OR_MANAGEMENT_ONLY"
    assert _classify_role("CFO", "X Inc") == "OFFICER_OR_MANAGEMENT_ONLY"
    assert _classify_role("Bookkeeper", "X") == "OFFICER_OR_MANAGEMENT_ONLY"
    assert _classify_role("", "X") == "NONE"


def test_display_name_fallback():
    assert _display_name(None, "Norman", "Pullen") == "Norman Pullen"
    assert _display_name("   ", "Julie Ann", "Pullen") == "Julie Ann Pullen"
    assert _display_name("Full Name", "x", "y") == "Full Name"


# --------------------------------------------------------------------------- integration
def test_pullen_safe_ownership_with_reconciliation():
    tag = "BRZ" + uuid.uuid4().hex[:8]
    try:
        with engine.begin() as c:
            hh = _mk_household(c, tag, "Pullen Household")
            norman = _mk_person(c, tag, "Norman", "Pullen", household_id=hh)      # canonical (household)
            _mk_person(c, tag, "Julie Ann", "Pullen", household_id=hh)            # spouse, no owner contact
            stale = _mk_person(c, tag, "Norm", "Pullen")                          # stale (no hh/doc)
            biz = _mk_business(c, f"Pullen Homes Inc {tag}")
            _mk_doc(c, tag, organization_id=biz, household_id=hh)                 # corroboration
            _mk_wb(c, tag, stale, "Norm", "Pullen", f"Pullen Homes Inc {tag}", "Owner")
        rep = resolve_business_relationships(business_ids=[biz])
        rec = rep["SAFE_OWNERSHIP"][0]
        assert rec["apply_eligible"] is True                                     # SAFE_OWNERSHIP → eligible
        assert [o["person_id"] for o in rec["owners"]] == [norman]               # owner = canonical, not stale
        assert rec["owners"][0]["role"] == "EXPLICIT_OWNERSHIP"
        assert rec["owners"][0]["requires_reconciliation"] and rec["owners"][0]["source_person_id"] == stale
        assert rec["reconciliations"][0]["canonical_person_id"] == norman
        assert any(h["household_id"] == hh and h["doc_coownership_count"] >= 1
                   for h in rec["household_associations"])
        assert all("ownership_percentage" not in o for o in rec["owners"])
    finally:
        _cleanup(tag)


def test_president_and_ceo_are_association_only():
    tag = "BRZ" + uuid.uuid4().hex[:8]
    try:
        with engine.begin() as c:
            hh = _mk_household(c, tag)
            barry = _mk_person(c, tag, "Barry", "Beckner", household_id=hh)
            biz = _mk_business(c, f"Cave Spring Painting {tag}")
            _mk_wb(c, tag, barry, "Barry", "Beckner", f"Cave Spring Painting {tag}", "President")
            hh2 = _mk_household(c, tag, "Keen")
            keen = _mk_person(c, tag, "Jeanie", "Keen", household_id=hh2)
            biz2 = _mk_business(c, f"Sunbelt Brokers {tag}")
            _mk_wb(c, tag, keen, "Jeanie", "Keen", f"Sunbelt Brokers {tag}", "CEO")
        rep = resolve_business_relationships(business_ids=[biz, biz2])
        names = {r["business_id"]: r for r in rep["SAFE_ASSOCIATION_ONLY"]}
        assert biz in names and biz2 in names
        assert names[biz]["owners"] == [] and names[biz]["associations"][0]["role"] == "OFFICER_OR_MANAGEMENT_ONLY"
        assert names[biz2]["owners"] == []
        assert names[biz]["apply_eligible"] is False and names[biz2]["apply_eligible"] is False
        assert biz not in {r["business_id"] for r in rep["SAFE_OWNERSHIP"]}
    finally:
        _cleanup(tag)


def test_founder_alone_is_association_only_not_apply_eligible():
    tag = "BRZ" + uuid.uuid4().hex[:8]
    try:
        with engine.begin() as c:
            fp = _mk_person(c, tag, "Fran", "Founderly", doc=True)
            biz = _mk_business(c, f"Founderly Labs LLC {tag}")
            _mk_wb(c, tag, fp, "Fran", "Founderly", f"Founderly Labs LLC {tag}", "Founder")
        rep = resolve_business_relationships(business_ids=[biz])
        rec = rep["SAFE_ASSOCIATION_ONLY"][0]
        assert rec["business_id"] == biz and rec["owners"] == []
        assert rec["associations"][0]["role"] == "OFFICER_OR_MANAGEMENT_ONLY"
        assert rec["apply_eligible"] is False
        assert biz not in {r["business_id"] for r in rep["SAFE_OWNERSHIP"]}
    finally:
        _cleanup(tag)


def test_compound_owner_title_is_explicit_ownership():
    tag = "BRZ" + uuid.uuid4().hex[:8]
    try:
        with engine.begin() as c:
            karl = _mk_person(c, tag, "Karl", "Armbrust", doc=True)              # canonical via a doc
            biz = _mk_business(c, f"Crossbow Services LLC {tag}")
            _mk_wb(c, tag, karl, "Karl", "Armbrust", f"Crossbow Services LLC {tag}",
                   "Owner-Construction-Handyman")
        rep = resolve_business_relationships(business_ids=[biz])
        rec = rep["SAFE_OWNERSHIP"][0]
        assert rec["apply_eligible"] is True
        assert [o["person_id"] for o in rec["owners"]] == [karl]
        assert rec["owners"][0]["role"] == "EXPLICIT_OWNERSHIP"
    finally:
        _cleanup(tag)


def test_two_independent_owners_is_safe_multi_owner():
    tag = "BRZ" + uuid.uuid4().hex[:8]
    try:
        with engine.begin() as c:
            a = _mk_person(c, tag, "Alan", "Mignard", doc=True)
            b = _mk_person(c, tag, "Beth", "Mignard", doc=True)
            biz = _mk_business(c, f"Mignard Company LLC {tag}")
            _mk_wb(c, tag, a, "Alan", "Mignard", f"Mignard Company LLC {tag}", "Owner")
            _mk_wb(c, tag, b, "Beth", "Mignard", f"Mignard Company LLC {tag}", "Owner")
        rep = resolve_business_relationships(business_ids=[biz])
        rec = rep["SAFE_OWNERSHIP"][0]
        assert rec["apply_eligible"] is True
        assert sorted(o["person_id"] for o in rec["owners"]) == sorted([a, b])
    finally:
        _cleanup(tag)


def test_stale_owner_without_canonical_is_person_identity_review():
    tag = "BRZ" + uuid.uuid4().hex[:8]
    try:
        with engine.begin() as c:
            stale = _mk_person(c, tag, "Andrew", "Grinder")                      # stale, no canonical twin
            biz = _mk_business(c, f"Grinder Works LLC {tag}")
            _mk_wb(c, tag, stale, "Andrew", "Grinder", f"Grinder Works LLC {tag}", "Owner")
        rep = resolve_business_relationships(business_ids=[biz])
        pir = {r["business_id"]: r for r in rep["PERSON_IDENTITY_REVIEW"]}
        assert biz in pir and pir[biz]["apply_eligible"] is False
        assert biz not in {r["business_id"] for r in rep["SAFE_OWNERSHIP"]}
    finally:
        _cleanup(tag)


def test_duplicate_normalized_businesses_go_to_review():
    tag = "BRZ" + uuid.uuid4().hex[:8]
    try:
        with engine.begin() as c:
            marty = _mk_person(c, tag, "Marty", "Maxwell", doc=True)
            b1 = _mk_business(c, f"Haul-Max Trucking LLC {tag}")
            b2 = _mk_business(c, f"HaulMax Trucking LLC {tag}")
            _mk_wb(c, tag, marty, "Marty", "Maxwell", f"Haul-Max Trucking LLC {tag}", "Owner")
        rep = resolve_business_relationships(business_ids=[b1, b2])
        review = {r["business_id"]: r for r in rep["DUPLICATE_BUSINESS_REVIEW"]}
        assert b1 in review and b2 in review
        assert review[b1]["apply_eligible"] is False and review[b2]["apply_eligible"] is False
        assert not ({b1, b2} & {r["business_id"] for r in rep["SAFE_OWNERSHIP"]})
    finally:
        _cleanup(tag)


def test_blank_full_name_falls_back_in_workspace():
    tag = "BRZ" + uuid.uuid4().hex[:8]
    try:
        with engine.begin() as c:
            norm = _mk_person(c, tag, "Norman", "Pullen", doc=True, full_name=None)  # blank full_name
            biz = _mk_business(c, f"Pullen Homes Inc {tag}")
            rt = c.execute(select(relationship_types.c.id).where(
                relationship_types.c.category.in_(("ownership", "org_structure")),
                relationship_types.c.code.in_(("owns", "owner"))).limit(1)).scalar()
            if rt is None:
                rt = c.execute(insert(relationship_types).values(
                    code="owns", name="Owns", inverse_name="Owned By", category="ownership",
                    directed=True, active=True).returning(relationship_types.c.id)).scalar_one()
            oe = ensure_person_entity(c, norm)
            rel = c.execute(insert(relationships).values(
                from_entity_id=oe, to_entity_id=biz, relationship_type_id=rt, active=True, source="test")
                .returning(relationships.c.id)).scalar_one()
            c.execute(insert(relationship_ownership).values(
                relationship_id=rel, is_direct=True, evidence_source="wealthbox"))
        ws = get_business_workspace(biz)
        assert ws["owners"][0]["person_id"] == norm
        assert ws["owners"][0]["name"] == f"Norman Pullen{tag}"     # first+last fallback, not None/'Person N'
    finally:
        _cleanup(tag)
