"""Sprint 2 (D-3) — search returns one row per canonical person, and the pg_trgm
indexes backing the ILIKE search exist.

A client that appears in more than one source system (e.g. Wealthbox and Schwab) is a single
canonical person; search must show them once, linking to the Client Profile, rather than once
per source system. Unlinked contacts are never merged.
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy import insert, text

from app.db import engine, households, people, person_source_links, source_contacts
from app.routes.search import _search


def _person(full_name):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"H {uuid.uuid4().hex[:6]}")
                        .returning(households.c.id)).scalar_one()
        return c.execute(people.insert().values(household_id=hid, full_name=full_name, active=True)
                         .returning(people.c.id)).scalar_one()


def _source_contact(*, full_name, source_system, email=None, phone=None, last_name=None):
    with engine.begin() as c:
        return c.execute(insert(source_contacts).values(
            source_system=source_system, source_file="test.csv", source_hash=uuid.uuid4().hex,
            raw_data=json.dumps({"name": full_name}), full_name=full_name, last_name=last_name,
            email=email, phone=phone).returning(source_contacts.c.id)).scalar_one()


def _link(person_id, source_contact_id):
    with engine.begin() as c:
        c.execute(insert(person_source_links).values(
            person_id=person_id, source_contact_id=source_contact_id,
            match_method="test", match_score=100, confirmed=True))


def test_same_person_across_two_systems_returns_one_row():
    tag = f"Zdedupe{uuid.uuid4().hex[:8]}"
    pid = _person(f"Robert {tag}")
    sc1 = _source_contact(full_name=f"Robert {tag}", source_system="wealthbox", last_name=tag)
    sc2 = _source_contact(full_name=f"Bob {tag}", source_system="schwab", last_name=tag)
    _link(pid, sc1)
    _link(pid, sc2)

    results = _search(tag)
    # exactly one row, and it points at the canonical person
    assert len(results) == 1
    assert results[0]["person_id"] == pid


def test_unlinked_contacts_are_not_merged():
    tag = f"Zunlinked{uuid.uuid4().hex[:8]}"
    _source_contact(full_name=f"Alice {tag}", source_system="wealthbox", last_name=tag)
    _source_contact(full_name=f"Alicia {tag}", source_system="schwab", last_name=tag)

    results = _search(tag)
    # both appear individually (no person link -> never collapsed)
    assert len(results) == 2
    assert all(r["person_id"] is None for r in results)


def test_search_matches_by_name_email_and_phone():
    tag = uuid.uuid4().hex[:8]
    email = f"carol.{tag}@example.com"
    phone = f"512555{tag[:4]}"
    _source_contact(full_name=f"Carol Z{tag}", source_system="wealthbox",
                    email=email, phone=phone, last_name=f"Z{tag}")
    assert any(r["full_name"] == f"Carol Z{tag}" for r in _search(f"Z{tag}"))   # by name
    assert any(r["email"] == email for r in _search(f"carol.{tag}"))           # by email
    assert len(_search(phone)) >= 1                                            # by phone


def test_trgm_indexes_exist():
    with engine.connect() as c:
        names = set(c.execute(text(
            "select indexname from pg_indexes where tablename='source_contacts' "
            "and indexname like '%trgm%'")).scalars().all())
    for column in ("full_name", "first_name", "last_name", "email", "phone", "city"):
        assert f"ix_source_contacts_{column}_trgm" in names


def test_person_row_links_to_profile_not_source():
    # regression on the Sprint 1 behaviour: a linked contact opens /people/{id}
    tag = f"Zlink{uuid.uuid4().hex[:8]}"
    pid = _person(f"Dana {tag}")
    sc = _source_contact(full_name=f"Dana {tag}", source_system="wealthbox", last_name=tag)
    _link(pid, sc)
    rows = _search(tag)
    assert len(rows) == 1 and rows[0]["person_id"] == pid and rows[0]["id"] == sc
