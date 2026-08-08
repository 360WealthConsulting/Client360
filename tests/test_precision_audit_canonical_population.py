"""Coverage for the READ-ONLY canonical-population precision audit."""
import os
import uuid

import pytest
from sqlalchemy import func, select

from app.db import engine, households, metadata, people
from scripts.migration.precision_audit_canonical_population import (
    audit,
    contact_detail,
    duplicate_groups,
    household_candidates,
    resolvable_folders,
)

source_contacts = metadata.tables["source_contacts"]
person_source_links = metadata.tables["person_source_links"]
documents = metadata.tables["documents"]
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


def _person(full_name, email=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, active=True,
                        normalized_email=email).returning(people.c.id)).scalar_one()
    _C["people"].append(pid); return pid


def _sc(full_name, system="Wealthbox", email=None, raw=None):
    with engine.begin() as c:
        sid = c.execute(source_contacts.insert().values(
            source_system=system, source_file="t", source_record_id=uuid.uuid4().hex,
            source_hash=uuid.uuid4().hex, full_name=full_name, normalized_email=email,
            raw_data=(raw or {})).returning(source_contacts.c.id)).scalar_one()
    _C["source_contacts"].append(sid); return sid


def _link(pid, sid):
    with engine.begin() as c:
        i = c.execute(person_source_links.insert().values(person_id=pid, source_contact_id=sid,
                      match_method="email", match_score=95, confirmed=True).returning(person_source_links.c.id)).scalar_one()
    _C["person_source_links"].append(i); return i


def _counts():
    with engine.connect() as c:
        return {t.name: int(c.execute(select(func.count()).select_from(t)).scalar_one())
                for t in (people, source_contacts, person_source_links, households, documents)}


# --------------------------------------------------------------------------- pure enumeration

def test_contact_detail_attaches_candidates_and_target():
    d = contact_detail({"id": 1, "source_system": "Wealthbox", "source_record_id": "r1",
                        "full_name": "C1", "normalized_email": "e1", "normalized_phone": None, "raw_data": {}},
                       {"e1": [11]}, {}, {}, {}, {11: "Target Person"})
    assert d["proposed_action"] == "existing_person_link"
    assert d["candidate_person_ids"] == "11" and d["target_name"] == "Target Person"


def test_duplicate_groups_and_households_and_resolvable():
    person_name = {1: "Dupe A", 2: "Dupe B", 10: "Aa Bb", 20: "Cc Bb"}
    dups = duplicate_groups({"dupe x": [1, 2]}, {}, {}, person_name)
    assert len(dups) == 1 and dups[0]["size"] == 2 and dups[0]["member_person_ids"] == "1;2"

    hh = household_candidates(["Aa and Cc Bb", "Solo Person"], {"aa bb": [10], "bb cc": [20]}, person_name)
    assert len(hh) == 1 and hh[0]["member_person_ids"] == "10;20"

    res = resolvable_folders(
        folders=["Promote Me", "Acme Biz", "Existing Person", "Nobody"],
        unique_existing={"existing person"}, promotable_unique={"me promote"},
        business_names={"acme biz"}, people_by_name={"existing person": [1]})
    paths = {r["folder"]: r["path"] for r in res}
    assert paths == {"Promote Me": "person_promotion_or_link", "Acme Biz": "business_canonicalization"}


# --------------------------------------------------------------------------- read-only DB audit

def test_audit_enumerates_and_is_readonly(tmp_path):
    p1 = _person(f"Linkme Personx {_TAG}", email=f"link{_TAG}@x.com")
    _link(p1, _sc(f"Linkme Personx {_TAG}", email=f"link{_TAG}@x.com"))          # already linked
    _sc(f"Linkme Personx {_TAG}", email=f"link{_TAG}@x.com")                     # -> existing_person_link
    _sc(f"Promoteme Uniqueperson {_TAG}", email=f"prom{_TAG}@x.com")            # -> safe_person_promotion
    _sc(f"Star City Heating {_TAG}", raw={"type": "Company"})                   # -> business
    _sc(f"Nameonly Nobody {_TAG}")                                             # -> unresolved
    _sc("Shared Onex", email=f"sh{_TAG}@x.com"); _sc("Shared Twox", email=f"sh{_TAG}@x.com")  # -> ambiguous x2
    _person(f"Dupe Personx {_TAG}"); _person(f"Dupe Personx {_TAG}")           # duplicate group
    _person(f"Aaa Bbb{_TAG}"); _person(f"Ccc Bbb{_TAG}")                       # household members

    (tmp_path / "exceptions.csv").write_text(
        "source_folder,resolution,reason\n"
        f"Promoteme Uniqueperson {_TAG},unmatched,x\n"
        f"Star City Heating {_TAG},unmatched,x\n"
        f"Aaa and Ccc Bbb{_TAG},unmatched,x\n", encoding="utf-8")
    out = tmp_path / "report"

    before = _counts()
    result = audit(str(tmp_path), engine=engine, out_dir=str(out))
    after = _counts()

    assert before == after                                                     # strictly read-only
    c = result["counts"]
    assert c["existing_person_link"] >= 1 and c["safe_person_promotion"] >= 1
    assert c["business_company_candidate"] >= 1 and c["ambiguous_identity"] >= 2 and c["unresolved"] >= 1
    assert c["duplicate_groups"] >= 1 and c["household_candidates"] >= 1
    assert c["resolvable_folders"] >= 3                                         # person + business + household

    link_row = next(d for d in result["by_action"]["existing_person_link"]
                    if d["source_name"] == f"Linkme Personx {_TAG}")
    assert link_row["target_name"] == f"Linkme Personx {_TAG}"
    biz_row = next(d for d in result["by_action"]["business_company_candidate"]
                   if d["source_name"] == f"Star City Heating {_TAG}")
    assert biz_row["proposed_canonical_target"] == "relationship_entities:business"

    for name in ("precision_contacts.csv", "precision_duplicate_groups.csv",
                 "precision_household_candidates.csv", "precision_resolvable_folders.csv"):
        assert os.path.isfile(out / name)
