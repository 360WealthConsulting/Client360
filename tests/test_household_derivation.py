"""Sprint 2 — household-derivation engine (mechanism complete; policy is a business decision).

The engine groups un-householded people by an injected policy. The default policy derives nothing;
the candidate address policy is exercised here to prove the mechanism, but is not auto-enabled.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db import engine, household_relationships, households, people
from app.services.household_derivation import (
    derive_households,
    group_by_normalized_address,
    no_derivation,
)


def _person(full_name, *, last_name=None, address=None, postal=None, household_id=None, active=True):
    with engine.begin() as c:
        return c.execute(people.insert().values(
            full_name=full_name, last_name=last_name, address_line_1=address, postal_code=postal,
            household_id=household_id, active=active).returning(people.c.id)).scalar_one()


def test_default_policy_derives_nothing():
    _person(f"A {uuid.uuid4().hex[:6]}", address="1 Main St", postal="00001")
    _person(f"B {uuid.uuid4().hex[:6]}", address="1 Main St", postal="00001")
    report = derive_households(no_derivation, dry_run=True)
    assert report.groups == 0 and report.households_created == 0


def test_dry_run_reports_groups_without_writing():
    tag = uuid.uuid4().hex[:8]
    addr, postal = f"{tag} Oak Ave", tag
    _person("Pat Vale", last_name="Vale", address=addr, postal=postal)
    _person("Sam Vale", last_name="Vale", address=addr, postal=postal)
    before = _household_count()
    report = derive_households(group_by_normalized_address, dry_run=True)
    assert report.groups >= 1 and report.members_assigned >= 2
    assert report.households_created == 0            # dry run writes nothing
    assert _household_count() == before


def test_apply_creates_household_and_assigns_members():
    tag = uuid.uuid4().hex[:8]
    addr, postal = f"{tag} Elm St", tag
    p1 = _person("Robin Kaye", last_name="Kaye", address=addr, postal=postal)
    p2 = _person("Jamie Kaye", last_name="Kaye", address=addr, postal=postal)
    _person(f"Loner {tag}", last_name="Solo", address=f"{tag} Nowhere", postal=tag + "z")  # singleton -> skipped

    report = derive_households(group_by_normalized_address, dry_run=False)
    assert report.households_created >= 1

    with engine.connect() as c:
        h1 = c.execute(select(people.c.household_id).where(people.c.id == p1)).scalar_one()
        h2 = c.execute(select(people.c.household_id).where(people.c.id == p2)).scalar_one()
        name = c.execute(select(households.c.name).where(households.c.id == h1)).scalar_one()
        members = c.execute(select(household_relationships.c.person_id).where(
            household_relationships.c.household_id == h1)).scalars().all()
    assert h1 is not None and h1 == h2               # both in the same household
    assert name == "Kaye Household"                  # shared last name
    assert set(members) == {p1, p2}


def test_already_householded_people_are_left_alone():
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"Existing {tag}").returning(households.c.id)).scalar_one()
    pid = _person("Already Placed", last_name="Placed", address=f"{tag} Set St", postal=tag,
                  household_id=hid)
    derive_households(group_by_normalized_address, dry_run=False)
    with engine.connect() as c:
        assert c.execute(select(people.c.household_id).where(people.c.id == pid)).scalar_one() == hid


def _household_count():
    with engine.connect() as c:
        from sqlalchemy import func
        return c.execute(select(func.count()).select_from(households)).scalar_one()
