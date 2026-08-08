"""Coverage for the READ-ONLY duplicate-people diagnosis + link validation."""
import uuid

import pytest
from sqlalchemy import func, select

from app.db import engine, metadata, people
from scripts.migration.diagnose_duplicate_people import (
    classify_name_group,
    diagnose,
    revalidate_promotions,
    validate_links,
)

source_contacts = metadata.tables["source_contacts"]
person_source_links = metadata.tables["person_source_links"]
_TAG = uuid.uuid4().hex[:8]
_C = {"people": [], "source_contacts": [], "person_source_links": []}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        for tbl, key in ((person_source_links, "person_source_links"),
                         (source_contacts, "source_contacts"), (people, "people")):
            if _C[key]:
                c.execute(tbl.delete().where(tbl.c.id.in_(_C[key])))
    for k in _C:
        _C[k].clear()


def _person(full_name, first=None, last=None, email=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, first_name=first, last_name=last,
                        active=True, normalized_email=email).returning(people.c.id)).scalar_one()
    _C["people"].append(pid); return pid


def _sc(full_name, system="Drake", record_id=None, first=None, last=None, email=None):
    with engine.begin() as c:
        sid = c.execute(source_contacts.insert().values(
            source_system=system, source_file=f"{system} file", source_record_id=record_id,
            source_hash=uuid.uuid4().hex, full_name=full_name, first_name=first, last_name=last,
            normalized_email=email, raw_data={}).returning(source_contacts.c.id)).scalar_one()
    _C["source_contacts"].append(sid); return sid


def _link(pid, sid, method="auto_promote"):
    with engine.begin() as c:
        i = c.execute(person_source_links.insert().values(person_id=pid, source_contact_id=sid,
                      match_method=method, match_score=100, confirmed=True).returning(person_source_links.c.id)).scalar_one()
    _C["person_source_links"].append(i); return i


def _counts():
    with engine.connect() as c:
        return {t.name: int(c.execute(select(func.count()).select_from(t)).scalar_one())
                for t in (people, source_contacts, person_source_links)}


# --------------------------------------------------------------------------- pure

def test_classify_name_group():
    assert classify_name_group({("Drake", "r1")}, {"auto_promote"}, 3) == "same_source_record_reimport"
    assert classify_name_group({("Drake", "r1"), ("Drake", "r2")}, {"auto_promote"}, 2) == "name_only_promotion"
    assert classify_name_group({("Wealthbox", "e1")}, {"auto_email_phone"}, 2) == "same_source_record_reimport"
    assert classify_name_group(set(), set(), 2) == "mixed_review"


# --------------------------------------------------------------------------- read-only DB

def test_diagnose_traces_and_validates_readonly():
    # same-source-record reimport group (deterministically collapsible): 2 people, same (Drake, REC1)
    p1 = _person(f"Adria Pratsx {_TAG}"); p2 = _person(f"Adria Pratsx {_TAG}")
    _link(p1, _sc(f"Adria Pratsx {_TAG}", record_id=f"REC1-{_TAG}"))
    _link(p2, _sc(f"Adria Pratsx {_TAG}", record_id=f"REC1-{_TAG}"))
    # name-only promotion group (needs review): 2 people, distinct record ids
    p3 = _person(f"Doug Knightx {_TAG}"); p4 = _person(f"Doug Knightx {_TAG}")
    _link(p3, _sc(f"Doug Knightx {_TAG}", record_id=f"R2-{_TAG}"))
    _link(p4, _sc(f"Doug Knightx {_TAG}", record_id=f"R3-{_TAG}"))
    # shared-email spouse pair (duplicate_email_group)
    _person(f"Betty Sharedx {_TAG}", first="Betty", last=f"Sharedx{_TAG}", email=f"fam{_TAG}@x.com")
    _person(f"William Sharedx {_TAG}", first="William", last=f"Sharedx{_TAG}", email=f"fam{_TAG}@x.com")
    # suspect existing_person_link: contact Betty -> person William via a uniquely-held email
    pw = _person(f"William Onlyx {_TAG}", first="William", last=f"Onlyx{_TAG}", email=f"one{_TAG}@x.com")
    _sc(f"Betty Onlyx {_TAG}", system="Wealthbox", first="Betty", last=f"Onlyx{_TAG}", email=f"one{_TAG}@x.com")
    # a clean safe promotion (unique email, no person)
    _sc(f"Promote Uniquex {_TAG}", system="Wealthbox", email=f"prom{_TAG}@x.com")

    before = _counts()
    d = diagnose(engine)
    links = validate_links(engine)
    proms = revalidate_promotions(engine)
    after = _counts()

    assert before == after                                              # strictly read-only
    groups = {tuple(sorted(g["member_person_ids"])): g for g in d["name_groups"]}
    assert groups[tuple(sorted([p1, p2]))]["classification"] == "same_source_record_reimport"
    assert groups[tuple(sorted([p3, p4]))]["classification"] == "name_only_promotion"
    assert d["deterministically_collapsible_excess"] >= 1
    assert d["needs_review_excess"] >= 1
    assert d["duplicate_email_groups"] >= 1

    suspect = next(r for r in links if r["target_person_id"] == pw)
    assert suspect["matched_on"] == "email" and suspect["same_last_name"] is True
    assert suspect["same_first_name"] is False and suspect["verdict"] == "suspect_household_shared"

    assert proms["safe_person_promotion"] >= 1
    assert proms["would_change"] == 0                                   # dedup wouldn't unsafe them
