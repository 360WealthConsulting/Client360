"""Coverage for the READ-ONLY Drake joint-return / household remediation planner.

Proves the deterministic couple classification (driven by stable Drake identifier hashes, never names),
the narrow provable-joint-document logic, and that planning performs zero writes.
"""
import hashlib
import uuid

import pytest
from sqlalchemy import func, select, text

from app.db import documents, engine, households, metadata, people
from scripts.migration.plan_joint_household_remediation import (
    classify_couple,
    doc_year,
    hash_status,
    is_return_document,
    plan,
)

_dcr = metadata.tables.get("drake_client_returns")
_di = metadata.tables.get("drake_identity")
_dimc = metadata.tables.get("drake_identity_match_candidates")
_TAG = uuid.uuid4().hex[:8]
_C = {"documents": [], "people": [], "hashes": [], "households": []}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _C["documents"]:
            c.execute(documents.delete().where(documents.c.id.in_(_C["documents"])))
        if _C["hashes"]:
            c.execute(_dcr.delete().where(_dcr.c.taxpayer_identifier_hash.in_(_C["hashes"])))
            c.execute(_dimc.delete().where(_dimc.c.identifier_hash.in_(_C["hashes"])))
            c.execute(_di.delete().where(_di.c.identifier_hash.in_(_C["hashes"])))
        if _C["people"]:
            c.execute(people.delete().where(people.c.id.in_(_C["people"])))
        if _C["households"]:
            c.execute(households.delete().where(households.c.id.in_(_C["households"])))
    for k in _C:
        _C[k].clear()


# --- pure classification ------------------------------------------------------

def test_hash_status():
    assert hash_status(None, {}, {}) == "missing"
    assert hash_status("h", {}, {}) == "insufficient"                       # no drake_identity
    assert hash_status("h", {"h": {"primary_person_id": 5, "taxpayer_name": "X"}}, {}) == "canonical"
    assert hash_status("h", {"h": {"primary_person_id": None, "taxpayer_name": "Sam"}}, {}) == "promotable"
    assert hash_status("h", {"h": {"primary_person_id": None, "taxpayer_name": "Sam"}},
                       {"h": [{"person_id": 9}]}) == "ambiguous"
    assert hash_status("h", {"h": {"primary_person_id": None, "taxpayer_name": ""}}, {}) == "insufficient"


def test_classify_couple_buckets():
    idx = {"tp": {"primary_person_id": 1, "taxpayer_name": "T"},
           "sp": {"primary_person_id": 2, "taxpayer_name": "S"}}
    # both canonical, shared household
    b, _ = classify_couple("tp", "sp", idx, {}, {1: {"household_id": 9}, 2: {"household_id": 9}})
    assert b == "already_correct_shared_household"
    # both canonical, no shared household
    b, _ = classify_couple("tp", "sp", idx, {}, {1: {"household_id": None}, 2: {"household_id": None}})
    assert b == "both_canonical_safe_household"
    # one canonical + one promotable
    idx2 = {"tp": {"primary_person_id": 1, "taxpayer_name": "T"},
            "sp": {"primary_person_id": None, "taxpayer_name": "Sam"}}
    b, _ = classify_couple("tp", "sp", idx2, {}, {1: {"household_id": None}})
    assert b == "one_canonical_plus_promotable"
    # both promotable
    idx3 = {"tp": {"primary_person_id": None, "taxpayer_name": "A"},
            "sp": {"primary_person_id": None, "taxpayer_name": "B"}}
    assert classify_couple("tp", "sp", idx3, {}, {})[0] == "both_promotable"
    # ambiguous (a candidate exists)
    assert classify_couple("tp", "sp", idx2, {"sp": [{"person_id": 7}]}, {1: {}})[0] == "ambiguous_hold"
    # insufficient (missing spouse hash / no provenance)
    assert classify_couple("tp", None, idx2, {}, {1: {}})[0] == "insufficient_provenance_hold"


def test_self_couple_both_hashes_same_person_is_no_action():
    # both stable hashes resolve to the SAME canonical person -> terminal no-action, NOT a household candidate
    idx = {"tp": {"primary_person_id": 42, "taxpayer_name": "T"},
           "sp": {"primary_person_id": 42, "taxpayer_name": "T"}}
    b, _ = classify_couple("tp", "sp", idx, {}, {42: {"household_id": 7}})
    assert b == "single_person_multi_hash_no_action"
    assert b != "both_canonical_safe_household"
    # even with no household on the shared person, it stays no-action (never a household candidate)
    assert classify_couple("tp", "sp", idx, {}, {42: {"household_id": None}})[0] \
        == "single_person_multi_hash_no_action"


def test_distinct_shared_household_still_already_correct():
    idx = {"tp": {"primary_person_id": 1, "taxpayer_name": "T"},
           "sp": {"primary_person_id": 2, "taxpayer_name": "S"}}
    assert classify_couple("tp", "sp", idx, {}, {1: {"household_id": 5}, 2: {"household_id": 5}})[0] \
        == "already_correct_shared_household"


def test_doc_year_and_return_detection():
    assert doc_year({"tags": {"tax_year": "2024"}, "original_name": "x.pdf"}) == 2024
    assert doc_year({"tags": {}, "original_name": "2023 Form 1040.pdf"}) == 2023
    assert doc_year({"tags": {}, "original_name": "no year.pdf"}) is None
    assert is_return_document({"tags": {"drake_doc_type": "federal_return"}, "original_name": "a"})
    assert is_return_document({"tags": {}, "category": "tax_document", "original_name": "a"})
    assert is_return_document({"tags": {}, "original_name": "2024 1040.pdf"})
    assert not is_return_document({"tags": {}, "original_name": "drivers license.pdf"})


# --- DB integration: Alicia/Samuel worked example ----------------------------

def _person(full_name, first, last):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, first_name=first, last_name=last,
                                               active=True).returning(people.c.id)).scalar_one()
    _C["people"].append(pid)
    return pid


def _identity(h, *, person_id=None, name=""):
    with engine.begin() as c:
        c.execute(_di.insert().values(identifier_hash=h, primary_person_id=person_id, taxpayer_name=name))
    _C["hashes"].append(h)


def _joint_return(year, row_no, tp_hash, sp_hash):
    with engine.begin() as c:
        c.execute(_dcr.insert().values(
            tax_year=year, source_row_number=row_no, taxpayer_identifier_hash=tp_hash,
            spouse_identifier_hash=sp_hash, taxpayer_first_name="Samuel", taxpayer_last_name="Robinson",
            spouse_first_name="Alicia", spouse_last_name="Robinson", filing_status="MFJ",
            source_updated_at=func.now(), raw_data={}))


def _tax_doc(person_id, year):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=None, organization_id=None,
            original_name=f"{year} Form 1040.pdf", stored_name=f"jh-{_TAG}-{uuid.uuid4().hex}",
            storage_path="x", storage_uri=f"C:\\Clients\\Robinson\\{year}-1040.pdf", size_bytes=10,
            sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active",
            category="tax_document", tags={"source_system": "Drake", "tax_year": str(year),
                                           "drake_doc_type": "federal_return"}).returning(documents.c.id)
        ).scalar_one()
    _C["documents"].append(did)
    return did


def test_alicia_samuel_worked_example_and_read_only():
    alicia = _person(f"Alicia Robinson {_TAG}", "Alicia", "Robinson")
    sam_hash = f"sam{_TAG}"           # taxpayer, no canonical person -> promotable
    ali_hash = f"ali{_TAG}"           # spouse, canonical Alicia
    _identity(sam_hash, person_id=None, name="Samuel Robinson")
    _identity(ali_hash, person_id=alicia, name="Alicia Robinson")
    for yr, n in ((2023, 1), (2024, 2), (2025, 3)):
        _joint_return(yr, int(f"9{_TAG[:4]}", 16) % 90000 + n, sam_hash, ali_hash)
    did = _tax_doc(alicia, 2024)

    before_docs = _count(documents)
    res = plan()

    couple = next((c for c in res["couple_rows"]
                   if c.get("taxpayer_hash") == sam_hash and c.get("spouse_hash") == ali_hash), None)
    assert couple is not None and couple["bucket"] == "one_canonical_plus_promotable"
    assert couple["spouse_person_id"] == alicia and couple["taxpayer_person_id"] is None
    assert set(couple["years"]) >= {2023, 2024, 2025}

    doc = next((d for d in res["doc_rows"] if d["document_id"] == did), None)
    assert doc is not None
    assert doc["current_owner"] == f"person:{alicia}"
    assert doc["proposed_household"] == "new (pending household creation)"
    assert doc["tax_year"] == 2024 and doc["relocation_required"] is True
    assert "MFJ couple" in doc["evidence"]

    # read-only: no documents created/removed by planning
    assert _count(documents) == before_docs
    with engine.connect() as c:
        still_owned = c.execute(select(documents.c.person_id).where(documents.c.id == did)).scalar_one()
    assert still_owned == alicia


def _count(tbl):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(tbl)).scalar_one()


# --- provable_joint_documents now means the Stage B deterministic proof standard -----------------------

def _household(name):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()
    _C["households"].append(hid)
    return hid


def _person_hh(full, first, last, hid):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full, first_name=first, last_name=last,
                                               active=True, household_id=hid).returning(people.c.id)).scalar_one()
    _C["people"].append(pid)
    return pid


def _named_doc(person_id, name, *, category="tax", drake_doc_type="federal_return", year="2024"):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=None, organization_id=None, original_name=name,
            stored_name=f"pj-{_TAG}-{uuid.uuid4().hex}", storage_path="x",
            storage_uri=f"C:\\Clients\\x\\{uuid.uuid4().hex}.pdf", size_bytes=10,
            sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active", category=category,
            tags={"source_system": "Drake", "tax_year": year, "drake_doc_type": drake_doc_type}
        ).returning(documents.c.id)).scalar_one()
    _C["documents"].append(did)
    return did


def test_provable_joint_documents_uses_stage_b_strict_standard():
    from app.services.migration import joint_document_reownership as jd
    hid = _household(f"Smith Household {_TAG}")
    john = _person_hh(f"John Smith {_TAG}", "John", f"Smith{_TAG}", hid)
    _person_hh(f"Jane Smith {_TAG}", "Jane", f"Smith{_TAG}", hid)          # distinct spouse, same household
    hj, hja = f"hj{_TAG}", f"hja{_TAG}"
    _identity(hj, person_id=john, name="John Smith")
    _identity(hja, person_id=_C["people"][-1], name="Jane Smith")
    _joint_return(2024, int(f"7{_TAG[:4]}", 16) % 90000 + 7, hj, hja)     # both canonical + shared HH

    # four person-owned docs owned by John; only the genuine joint 1040 is strictly re-ownable.
    genuine = _named_doc(john, f"2024 Form 1040 (John Smith{_TAG} and Jane Smith{_TAG}).pdf")   # both spouses
    business = _named_doc(john, f"2024 Form 1120 (Acme{_TAG} LLC).pdf")                          # business
    w2 = _named_doc(john, f"2024 W-2 {_TAG}.pdf", category="tax_document", drake_doc_type="")    # tax_document only
    single = _named_doc(john, f"2024 Form 1040 (John Smith{_TAG} only).pdf", drake_doc_type="")  # single-name

    res = plan()
    strict = jd.preview()
    reownable_ids = {r["document_id"] for r in strict["reownable_rows"]}

    # provable == Stage B strict, and ONLY the genuine joint personal return qualifies
    assert res["provable_joint_documents"] == strict["reownable"]
    assert genuine in reownable_ids
    assert business not in reownable_ids and w2 not in reownable_ids and single not in reownable_ids

    # the BROAD survey (permissive) still counts all four (incl. category='tax_document' alone + business)
    candidate_ids = {d["document_id"] for d in res["doc_rows"]}
    assert {genuine, business, w2, single} <= candidate_ids

    # after re-owning the genuine joint doc to the household, it leaves the person-owned re-ownable set
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == genuine)
                  .values(household_id=hid, person_id=None))
    assert genuine not in {r["document_id"] for r in jd.preview()["reownable_rows"]}


def test_drake_tables_present():
    # the planner depends on the Drake schema (drake01); guard that it is provisioned in the test DB.
    with engine.connect() as c:
        for t in ("drake_client_returns", "drake_identity", "drake_identity_match_candidates"):
            assert c.execute(text("SELECT to_regclass(:n)"), {"n": f"public.{t}"}).scalar() is not None
