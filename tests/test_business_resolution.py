"""Business Relationship Resolution — PREVIEW resolver, business workspace, and search wiring.

Pullen Homes is the mandatory regression fixture: Wealthbox says a stale 'Norm Pullen' (job_title=Owner,
company_name='Pullen Homes Inc') should reconcile to the canonical 'Norman Pullen' (household member),
and the business associates with the household — while the spouse is NOT auto-labelled owner and no
ownership percentage is invented. All fixtures are TAG-unique and torn down (no cross-test leakage).
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
from app.security.models import Principal
from app.services.business_resolution import resolve_business_relationships
from app.services.business_workspace import get_business_workspace
from app.services.relationships import ensure_person_entity

CAPS = frozenset({"client.read", "record.read_all", "organization.read"})


def _ownership_type_id(conn):
    rid = conn.execute(select(relationship_types.c.id).where(
        relationship_types.c.category.in_(("ownership", "org_structure")),
        relationship_types.c.code.in_(("owns", "owner"))).limit(1)).scalar()
    if rid:
        return rid
    return conn.execute(insert(relationship_types).values(
        code="owns", name="Owns", inverse_name="Owned By", category="ownership",
        directed=True, active=True).returning(relationship_types.c.id)).scalar_one()


def _mk_doc(conn, tag, *, organization_id=None, household_id=None, person_id=None):
    u = uuid.uuid4().hex
    return conn.execute(insert(documents).values(
        original_name=f"{tag} Pullen Homes Inc return.pdf", stored_name=f"brz:{u}",
        storage_path=f"/x/{u}", size_bytes=1, sha256=u.ljust(64, "0")[:64], status="active",
        organization_id=organization_id, household_id=household_id, person_id=person_id,
    ).returning(documents.c.id)).scalar_one()


def _make_pullen(*, with_wealthbox=True, owner_title="Owner"):
    tag = "BRZ" + uuid.uuid4().hex[:8]
    ids = {"tag": tag}
    with engine.begin() as c:
        hh = c.execute(insert(households).values(name=f"{tag} Pullen Household")
                       .returning(households.c.id)).scalar_one()
        norm = c.execute(insert(people).values(first_name="Norman", last_name=f"Pullen{tag}",
                         full_name=f"Norman Pullen{tag}", household_id=hh, active=True)
                         .returning(people.c.id)).scalar_one()
        julie = c.execute(insert(people).values(first_name="Julie Ann", last_name=f"Pullen{tag}",
                          full_name=f"Julie Ann Pullen{tag}", household_id=hh, active=True)
                          .returning(people.c.id)).scalar_one()
        stale = c.execute(insert(people).values(first_name="Norm", last_name=f"Pullen{tag}",
                          full_name=f"Norm Pullen{tag}", household_id=None, active=True)
                          .returning(people.c.id)).scalar_one()
        for pid in (norm, julie):
            c.execute(insert(household_relationships).values(
                household_id=hh, person_id=pid, relationship_type="member",
                is_primary=(pid == norm), is_primary_household=True))
        biz = c.execute(insert(relationship_entities).values(
            entity_type="business", name=f"Pullen Homes Inc {tag}", active=True)
            .returning(relationship_entities.c.id)).scalar_one()
        _mk_doc(c, tag, organization_id=biz, household_id=hh)   # (E) document co-ownership corroboration
        if with_wealthbox:
            sc = c.execute(insert(source_contacts).values(
                source_system="Wealthbox", source_file="wb", source_hash=uuid.uuid4().hex,
                first_name="Norm", last_name=f"Pullen{tag}", full_name=f"Norm Pullen{tag}",
                raw_data={"company_name": f"Pullen Homes Inc {tag}", "job_title": owner_title})
                .returning(source_contacts.c.id)).scalar_one()
            c.execute(insert(person_source_links).values(
                person_id=stale, source_contact_id=sc, match_method="auto_promote", confirmed=False))
        ids.update(hh=hh, norm=norm, julie=julie, stale=stale, biz=biz)
    return ids


def _teardown(ids):
    tag = ids["tag"]
    with engine.begin() as c:
        ent_ids = list(c.scalars(select(relationship_entities.c.id).where(
            relationship_entities.c.name.like(f"%{tag}%"))))
        ent_ids += list(c.scalars(select(relationship_entities.c.id).where(
            relationship_entities.c.person_id.in_([ids["norm"], ids["julie"], ids["stale"]]))))
        ent_ids += list(c.scalars(select(relationship_entities.c.id).where(
            relationship_entities.c.household_id == ids["hh"])))
        if ent_ids:
            c.execute(delete(relationship_ownership).where(relationship_ownership.c.relationship_id.in_(
                select(relationships.c.id).where(relationships.c.from_entity_id.in_(ent_ids)
                                                 | relationships.c.to_entity_id.in_(ent_ids)))))
            c.execute(delete(relationships).where(relationships.c.from_entity_id.in_(ent_ids)
                                                  | relationships.c.to_entity_id.in_(ent_ids)))
            c.execute(delete(relationship_entities).where(relationship_entities.c.id.in_(ent_ids)))
        c.execute(delete(documents).where(documents.c.household_id == ids["hh"]))
        c.execute(delete(person_source_links).where(person_source_links.c.person_id.in_(
            [ids["norm"], ids["julie"], ids["stale"]])))
        c.execute(delete(source_contacts).where(source_contacts.c.full_name.like(f"%{tag}%")))
        c.execute(delete(household_relationships).where(household_relationships.c.household_id == ids["hh"]))
        c.execute(delete(people).where(people.c.id.in_([ids["norm"], ids["julie"], ids["stale"]])))
        c.execute(delete(households).where(households.c.id == ids["hh"]))


def test_pullen_resolves_safe_with_reconciliation_and_household_assoc():
    ids = _make_pullen()
    try:
        rep = resolve_business_relationships(business_ids=[ids["biz"]])
        assert len(rep["safe"]) == 1 and not rep["ambiguous"] and not rep["unresolved"]
        rec = rep["safe"][0]
        # owner attaches to the CANONICAL Norman (7783-analog), not the stale Wealthbox person
        assert [o["person_id"] for o in rec["owners"]] == [ids["norm"]]
        owner = rec["owners"][0]
        assert owner["role"] == "owner" and owner["requires_reconciliation"] is True
        assert owner["source_person_id"] == ids["stale"]
        # reconciliation flagged: stale Norm -> canonical Norman
        assert rec["reconciliations"] == [r for r in rec["reconciliations"]
                                          if r["stale_person_id"] == ids["stale"]
                                          and r["canonical_person_id"] == ids["norm"]]
        assert rec["reconciliations"] and rec["reconciliations"][0]["canonical_person_id"] == ids["norm"]
        # household association present (with document co-ownership corroboration)
        assert any(h["household_id"] == ids["hh"] and h["doc_coownership_count"] >= 1
                   for h in rec["household_associations"])
        # spouse is NOT an owner and NO ownership percentage invented
        assert ids["julie"] not in [o["person_id"] for o in rec["owners"]]
        assert all("ownership_percentage" not in o for o in rec["owners"])
    finally:
        _teardown(ids)


def test_business_without_owner_title_is_not_owner():
    ids = _make_pullen(owner_title="Bookkeeper")     # association evidence, not ownership
    try:
        rep = resolve_business_relationships(business_ids=[ids["biz"]])
        rec = (rep["safe"] + rep["ambiguous"])[0]
        assert rec["owners"] == []                    # no owner role
        assert any(a["person_id"] == ids["norm"] for a in rec["associations"])
    finally:
        _teardown(ids)


def test_business_with_no_crm_evidence_is_unresolved():
    ids = _make_pullen(with_wealthbox=False)
    try:
        rep = resolve_business_relationships(business_ids=[ids["biz"]])
        assert len(rep["unresolved"]) == 1 and not rep["safe"]
    finally:
        _teardown(ids)


def test_business_workspace_shows_owner_household_and_docs():
    ids = _make_pullen()
    try:
        # apply one owner edge (as record_ownership would) to exercise the read/render path
        with engine.begin() as c:
            rt = _ownership_type_id(c)
            owner_entity = ensure_person_entity(c, ids["norm"])
            rel = c.execute(insert(relationships).values(
                from_entity_id=owner_entity, to_entity_id=ids["biz"], relationship_type_id=rt,
                active=True, source="test").returning(relationships.c.id)).scalar_one()
            c.execute(insert(relationship_ownership).values(
                relationship_id=rel, is_direct=True, evidence_source="wealthbox"))
        ws = get_business_workspace(ids["biz"])
        assert ws is not None and ws["name"].endswith(ids["tag"])
        assert [o["person_id"] for o in ws["owners"]] == [ids["norm"]]
        assert ws["owners"][0]["workspace_url"] == f"/client/{ids['norm']}"
        assert any(h["household_id"] == ids["hh"] for h in ws["related_households"])
        assert ws["document_count"] >= 1 and ws["documents"]
    finally:
        _teardown(ids)


def test_universal_search_business_url_and_hides_inactive_person():
    ids = _make_pullen()
    # add an inactive company-as-person row that must NOT appear as a normal Person result
    with engine.begin() as c:
        ghost = c.execute(insert(people).values(
            full_name=f"Pullen Homes Inc {ids['tag']}", active=False)
            .returning(people.c.id)).scalar_one()
    ids["ghost"] = ghost
    try:
        from app.services.universal_search import universal_search
        p = Principal(1, "a@e.test", "Adv", CAPS)
        data = universal_search(p, f"Pullen Homes Inc {ids['tag']}")
        biz_hits = [r for r in data["results"] if r["kind"] == "business" and r["id"] == ids["biz"]]
        assert biz_hits and biz_hits[0]["workspace_url"] == f"/business/{ids['biz']}"
        # inactive company-as-person is suppressed by default
        assert not any(r["kind"] == "person" and r["id"] == ghost for r in data["results"])
        # ...but is retrievable when archived/inactive are explicitly requested
        data2 = universal_search(p, f"Pullen Homes Inc {ids['tag']}", include_archived=True)
        assert any(r["kind"] == "person" and r["id"] == ghost for r in data2["results"])
    finally:
        with engine.begin() as c:
            c.execute(delete(people).where(people.c.id == ids["ghost"]))
        _teardown(ids)
