"""Canonical person display name across the workspace read models.

``people.full_name`` is a convenience column that is not populated for every canonical person; many
rows carry only ``first_name`` / ``last_name``. Read models that rendered ``full_name`` bare produced
``None`` (household member rows) or fell through to a ``"<entity_type> <id>"`` placeholder (the person
workspace heading) for exactly those people.

These fixtures deliberately create people with **full_name NULL and first/last populated** — the
production shape. The pre-existing client360/household suites did the inverse (full_name set,
first/last never set), which is why they never caught this.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, insert, select

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
from app.security.models import Principal
from app.services.client360 import get_workspace
from app.services.person_names import UNNAMED, person_display_name, person_row_display_name

ADMIN = Principal(1, "admin@t", "Admin",
                  frozenset({"client.read", "record.read_all", "record.write_all",
                             "organization.read", "organization.write"}))

_TAGS: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for tag in _TAGS:
        like = f"%{tag}%"
        with engine.begin() as c:
            ppl = list(c.scalars(select(people.c.id).where(people.c.last_name.like(like))))
            hhs = list(c.scalars(select(households.c.id).where(households.c.name.like(like))))
            ents = list(c.scalars(select(relationship_entities.c.id)
                                  .where(relationship_entities.c.name.like(like))))
            if ppl:
                ents += list(c.scalars(select(relationship_entities.c.id)
                                       .where(relationship_entities.c.person_id.in_(ppl))))
            ents = list(set(ents))
            if ents:
                rels = list(c.scalars(select(relationships.c.id).where(
                    relationships.c.from_entity_id.in_(ents) | relationships.c.to_entity_id.in_(ents))))
                if rels:
                    c.execute(relationship_ownership.delete().where(
                        relationship_ownership.c.relationship_id.in_(rels)))
                    c.execute(relationships.delete().where(relationships.c.id.in_(rels)))
                c.execute(documents.delete().where(documents.c.organization_id.in_(ents)))
                c.execute(relationship_entities.delete().where(relationship_entities.c.id.in_(ents)))
            if ppl:
                c.execute(household_relationships.delete()
                          .where(household_relationships.c.person_id.in_(ppl)))
                c.execute(people.delete().where(people.c.id.in_(ppl)))
            if hhs:
                c.execute(household_relationships.delete()
                          .where(household_relationships.c.household_id.in_(hhs)))
                c.execute(households.delete().where(households.c.id.in_(hhs)))
    _TAGS.clear()



def _tag():
    t = "PDN" + uuid.uuid4().hex[:8]
    _TAGS.append(t)
    return t


def _person_no_full_name(c, tag, first, last, *, household_id=None, is_primary=False):
    """The PRODUCTION shape: full_name NULL, first/last populated."""
    pid = c.execute(insert(people).values(
        first_name=first, last_name=f"{last}{tag}", full_name=None,
        household_id=household_id, active=True).returning(people.c.id)).scalar_one()
    if household_id:
        c.execute(insert(household_relationships).values(
            household_id=household_id, person_id=pid, relationship_type="member",
            is_primary=is_primary, is_primary_household=True))
    return pid


def _household(c, tag, name):
    return c.execute(insert(households).values(name=f"{tag} {name}")
                     .returning(households.c.id)).scalar_one()


def _business_owned_by(c, tag, person_id, name):
    from app.services.relationships import ensure_person_entity
    biz = c.execute(insert(relationship_entities).values(
        entity_type="business", name=f"{name} {tag}", active=True)
        .returning(relationship_entities.c.id)).scalar_one()
    rt = c.execute(select(relationship_types.c.id).where(
        relationship_types.c.category.in_(("ownership", "org_structure")),
        relationship_types.c.code == "owns").limit(1)).scalar_one()
    rel = c.execute(insert(relationships).values(
        from_entity_id=ensure_person_entity(c, person_id), to_entity_id=biz,
        relationship_type_id=rt, active=True, source="test").returning(relationships.c.id)).scalar_one()
    c.execute(insert(relationship_ownership).values(
        relationship_id=rel, is_direct=True,
        evidence_source="business_resolution:wealthbox.company_name+job_title"))
    return biz


# ------------------------------------------------------------------ unit: the canonical helper
def test_helper_prefers_full_name_then_first_last_then_fallback():
    assert person_display_name("Norman Pullen", "Norman", "Pullen") == "Norman Pullen"
    assert person_display_name(None, "Norman", "Pullen") == "Norman Pullen"
    assert person_display_name("   ", "Norman", "Pullen") == "Norman Pullen"
    assert person_display_name(None, "Norman", None) == "Norman"
    assert person_display_name(None, None, None, fallback="Acme Household") == "Acme Household"
    assert person_display_name(None, None, None) == UNNAMED
    assert person_display_name(None, None, None) is not None


def test_helper_row_form_tolerates_missing_columns():
    assert person_row_display_name({"full_name": None, "first_name": "A", "last_name": "B"}) == "A B"
    assert person_row_display_name({"full_name": "C D"}) == "C D"          # narrow SELECT degrades
    assert person_row_display_name(None, fallback="x") == "x"


# ------------------------------------------------------------------ 1. person workspace heading
def test_person_workspace_renders_the_name_not_a_person_id_placeholder():
    tag = _tag()
    with engine.begin() as c:
        pid = _person_no_full_name(c, tag, "Norman", "Pullen")
    ws = get_workspace(ADMIN, person_id=pid)
    assert ws is not None
    assert ws["display_name"] == f"Norman Pullen{tag}"
    assert ws["display_name"] != f"person {pid}"
    assert str(pid) not in ws["display_name"]


# ------------------------------------------------------------------ 2/3. household By-member names
def test_household_financial_by_member_renders_real_names_not_none():
    tag = _tag()
    with engine.begin() as c:
        hh = _household(c, tag, "Norman & Julie Ann Pullen Household")
        norman = _person_no_full_name(c, tag, "Norman", "Pullen", household_id=hh, is_primary=True)
        julie = _person_no_full_name(c, tag, "Julie Ann", "Pullen", household_id=hh)
    ws = get_workspace(ADMIN, household_id=hh)
    assert ws is not None

    by_member = ws["sections"]["financial"]["members"]
    assert len(by_member) == 2
    names = {m["person_id"]: m["name"] for m in by_member}
    assert names[norman] == f"Norman Pullen{tag}"
    assert names[julie] == f"Julie Ann Pullen{tag}"
    assert None not in names.values() and "None" not in names.values()

    # the member directory and the household title are correct too
    directory = {m["person_id"]: m["name"] for m in ws["sections"]["members"]["directory"]}
    assert directory[norman] == f"Norman Pullen{tag}"
    assert directory[julie] == f"Julie Ann Pullen{tag}"
    assert ws["display_name"] == f"{tag} Norman & Julie Ann Pullen Household"


def test_household_title_behavior_unchanged_for_household_subject():
    """A household subject has no first/last; display_name must still be households.name."""
    tag = _tag()
    with engine.begin() as c:
        hh = _household(c, tag, "Title Only Household")
        _person_no_full_name(c, tag, "Solo", "Member", household_id=hh, is_primary=True)
    ws = get_workspace(ADMIN, household_id=hh)
    assert ws["display_name"] == f"{tag} Title Only Household"


# ------------------------------------------------------------------ 4. business workspace owner
def test_business_workspace_still_renders_the_owner_name():
    from app.services.business_workspace import get_business_workspace
    tag = _tag()
    with engine.begin() as c:
        hh = _household(c, tag, "Norman & Julie Ann Pullen Household")
        norman = _person_no_full_name(c, tag, "Norman", "Pullen", household_id=hh, is_primary=True)
        _person_no_full_name(c, tag, "Julie Ann", "Pullen", household_id=hh)
        biz = _business_owned_by(c, tag, norman, "Pullen Homes Inc")
    ws = get_business_workspace(biz)
    assert [o["person_id"] for o in ws["owners"]] == [norman]
    assert ws["owners"][0]["name"] == f"Norman Pullen{tag}"
    assert ws["owners"][0]["workspace_url"] == f"/client/{norman}"
    assert [h["household_id"] for h in ws["related_households"]] == [hh]


# ------------------------------------------------------------------ 6. reads mutate nothing
def test_reads_do_not_mutate_any_row():
    tag = _tag()
    with engine.begin() as c:
        hh = _household(c, tag, "No Mutation Household")
        norman = _person_no_full_name(c, tag, "Norman", "Pullen", household_id=hh, is_primary=True)
        julie = _person_no_full_name(c, tag, "Julie Ann", "Pullen", household_id=hh)
        biz = _business_owned_by(c, tag, norman, "Pullen Homes Inc")

    def snapshot():
        with engine.connect() as c:
            ppl = [dict(r) for r in c.execute(
                select(people).where(people.c.id.in_([norman, julie]))).mappings()]
            hhr = sorted(c.execute(select(household_relationships.c.person_id,
                                          household_relationships.c.household_id,
                                          household_relationships.c.relationship_type,
                                          household_relationships.c.is_primary)
                                   .where(household_relationships.c.household_id == hh)).all())
            hrow = dict(c.execute(select(households).where(households.c.id == hh)).mappings().one())
            rels = sorted(c.execute(select(relationships.c.id, relationships.c.from_entity_id,
                                           relationships.c.to_entity_id, relationships.c.active)
                                    .where(relationships.c.to_entity_id == biz)).all())
            own = sorted(c.execute(select(relationship_ownership.c.relationship_id,
                                          relationship_ownership.c.ownership_percentage,
                                          relationship_ownership.c.evidence_source)).all())
            docs = c.scalar(select(func.count()).select_from(documents)
                            .where(documents.c.organization_id == biz)) or 0
        return ppl, hhr, hrow, rels, own, docs

    before = snapshot()
    from app.services.business_workspace import get_business_workspace
    for _ in range(2):
        get_workspace(ADMIN, person_id=norman)
        get_workspace(ADMIN, household_id=hh)
        get_business_workspace(biz)
    assert snapshot() == before
