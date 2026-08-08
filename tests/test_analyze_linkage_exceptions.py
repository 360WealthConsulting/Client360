"""Coverage for the READ-ONLY linkage-exception analysis tool.

Proves the classification logic (pure) and that the DB-backed analysis is strictly read-only (SELECT only;
no rows added/changed/removed) and never runs remediation apply.
"""
import uuid

import pytest
from sqlalchemy import func, select

from app.db import documents, engine, households, metadata, people
from scripts.migration.analyze_linkage_exceptions import (
    analyze,
    build_indexes,
    classify_all,
    read_exception_folders,
)

relationship_entities = metadata.tables["relationship_entities"]
import_jobs = metadata.tables["import_jobs"]
_TAG = uuid.uuid4().hex[:8]
_CREATED = {"people": [], "households": [], "relationship_entities": []}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        for tbl, key in ((relationship_entities, "relationship_entities"),
                         (people, "people"), (households, "households")):
            if _CREATED[key]:
                c.execute(tbl.delete().where(tbl.c.id.in_(_CREATED[key])))
    for k in _CREATED:
        _CREATED[k].clear()


def _person(full_name, contact_type=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, active=True,
                                               contact_type=contact_type).returning(people.c.id)).scalar_one()
    _CREATED["people"].append(pid); return pid


def _household(name):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()
    _CREATED["households"].append(hid); return hid


def _org(name):
    with engine.begin() as c:
        oid = c.execute(relationship_entities.insert().values(entity_type="organization", name=name,
                                                              active=True).returning(relationship_entities.c.id)).scalar_one()
    _CREATED["relationship_entities"].append(oid); return oid


def _counts():
    with engine.connect() as c:
        return {t.name: int(c.execute(select(func.count()).select_from(t)).scalar_one())
                for t in (people, households, relationship_entities, documents, import_jobs)}


# --------------------------------------------------------------------------- pure classification

def test_classification_buckets():
    person_keys, hh_keys, org_keys = build_indexes(
        people=[("Star City Heating", "Company"), ("Michael White", None)],
        household_names=["Napier"],
        org_names=["Acme Holdings"],
    )
    folders = ["Napier Family", "Star City Heating", "Acme Holdings", "Michael and Debra White",
               "Smith Family", "Bob and Alice Zzz", "Client 12345", "Nonexistent Person"]
    pat, ex = classify_all(folders, person_keys, hh_keys, org_keys)
    assert pat["would_match_household_name"] == 1        # "Napier Family" -> household "Napier"
    assert pat["would_match_in_people"] == 1             # "Star City Heating"
    assert pat["would_match_org_registry"] == 1          # "Acme Holdings"
    assert pat["joint_partial_member_match"] == 1        # one member ("Michael White") exists
    assert pat["family_household_style_no_match"] == 1   # "Smith Family" (no household "Smith")
    assert pat["joint_no_member_match"] == 1             # "Bob and Alice Zzz"
    assert pat["has_digits_or_label"] == 1               # "Client 12345"
    assert pat["genuinely_absent"] == 1                  # "Nonexistent Person"
    assert any("Company" in e for e in ex["would_match_in_people"])   # contact_type surfaced


def test_read_exception_folders(tmp_path):
    (tmp_path / "exceptions.csv").write_text(
        "source_folder,resolution,reason\nFoo,unmatched,x\nBar Family,ambiguous,y\n", encoding="utf-8")
    assert read_exception_folders(str(tmp_path)) == ["Foo", "Bar Family"]


# --------------------------------------------------------------------------- read-only DB analysis

def test_analyze_is_readonly_and_classifies(tmp_path):
    _household(f"Napier {_TAG}")
    _person(f"Star City Heating {_TAG}", contact_type="Company")
    _org(f"Acme Holdings {_TAG}")
    (tmp_path / "exceptions.csv").write_text(
        "source_folder,resolution,reason\n"
        f"Napier {_TAG} Family,unmatched,x\n"
        f"Star City Heating {_TAG},unmatched,x\n"
        f"Acme Holdings {_TAG},unmatched,x\n"
        "Qxzvborg Absentfolkperson,unmatched,x\n", encoding="utf-8")   # digit-free, clearly absent

    before = _counts()
    result = analyze(str(tmp_path), engine=engine)
    after = _counts()

    assert before == after                                # strictly read-only: nothing added/changed/removed
    assert result["folders_analyzed"] == 4
    assert result["patterns"]["would_match_household_name"] >= 1
    assert result["patterns"]["would_match_in_people"] >= 1
    assert result["patterns"]["would_match_org_registry"] >= 1
    assert result["patterns"]["genuinely_absent"] >= 1
    assert result["index_sizes"]["people"] >= 1
