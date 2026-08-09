"""Coverage for Stage B joint personal-return document re-ownership (preview + guarded primitive).

Worked examples: Robinson document 800 (joint personal 1040 naming both spouses) is re-ownable to the
household; document 799 (INTEGRITY COATINGS LLC) is excluded as a business/entity return even though it is
a tax document in Alicia's folder. Proves the deterministic classifier, the read-only preview, and the
fail-closed / idempotent APPLY that changes ownership only (no storage_uri/document_sources/file changes).
"""
import hashlib
import os
import uuid

import pytest
from sqlalchemy import func, select

from app.db import documents, engine, households, metadata, people
from app.services.migration.joint_document_reownership import (
    ReownershipGuardError,
    apply,
    classify_document,
    is_business_document,
    is_personal_return,
    joint_signature,
    preview,
)

_dcr = metadata.tables.get("drake_client_returns")
_di = metadata.tables.get("drake_identity")
_TAG = uuid.uuid4().hex[:8]
_C = {"documents": [], "people": [], "households": [], "hashes": []}
_BACKUP = os.path.join(os.path.dirname(__file__), f"_reown_backup_{_TAG}.dump")


@pytest.fixture(scope="module", autouse=True)
def _backup_file():
    with open(_BACKUP, "w") as f:
        f.write("not-a-real-backup")
    yield
    os.remove(_BACKUP)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _C["documents"]:
            c.execute(documents.delete().where(documents.c.id.in_(_C["documents"])))
        if _C["people"]:
            c.execute(people.delete().where(people.c.id.in_(_C["people"])))
        if _C["hashes"]:
            c.execute(_dcr.delete().where(_dcr.c.taxpayer_identifier_hash.in_(_C["hashes"])))
            c.execute(_di.delete().where(_di.c.identifier_hash.in_(_C["hashes"])))
        if _C["households"]:
            c.execute(households.delete().where(households.c.id.in_(_C["households"])))
    for k in _C:
        _C[k].clear()


# --- pure classification ------------------------------------------------------

_ALICIA = {"first_name": "Alicia", "last_name": "Robinson"}
_SAMUEL = {"first_name": "Samuel", "last_name": "Robinson"}
_MEMBERS = [dict(_ALICIA, household_id=1), dict(_SAMUEL, household_id=1)]


def test_is_business_document():
    assert is_business_document({"original_name": "2025 Tax Return (INTEGRITY COATINGS LLC).pdf"})
    assert is_business_document({"original_name": "x.pdf", "tags": {"return_type": "1065"}})
    assert not is_business_document({"original_name": "2025 Form 1040 (Robinson).pdf"})


def test_is_personal_return_and_joint_signature():
    assert is_personal_return({"original_name": "2025 Form 1040.pdf", "tags": {}})
    assert not is_personal_return({"original_name": "2025 1120 (Acme LLC).pdf", "tags": {}})
    name = "2025 Tax Return Documents (ROBINSON, SAMUEL M & ALICIA L).pdf"
    assert joint_signature(name, _MEMBERS)
    assert not joint_signature("2025 Form 1040 (Alicia Robinson).pdf", _MEMBERS)   # only one spouse


def test_classify_document_buckets():
    person = {"household_id": 1}
    mfj = {2025}
    good = {"original_name": "2025 Tax Return Documents (ROBINSON, SAMUEL M & ALICIA L).pdf",
            "tags": {"tax_year": "2025"}}
    assert classify_document(good, person, _MEMBERS, mfj) == ("reownable", "proven_joint_personal_return")
    biz = {"original_name": "2025 Tax Return Documents (INTEGRITY COATINGS LLC).pdf", "tags": {"tax_year": "2025"}}
    assert classify_document(biz, person, _MEMBERS, mfj)[1] == "business_entity_return"
    solo = {"original_name": "2025 Form 1040 (Alicia Robinson).pdf", "tags": {"tax_year": "2025"}}
    assert classify_document(solo, person, _MEMBERS, mfj)[1] == "no_joint_signature"
    wrongyear = dict(good, tags={"tax_year": "2019"})
    assert classify_document(wrongyear, person, _MEMBERS, mfj)[1] == "year_not_a_joint_filing"
    assert classify_document(good, {"household_id": None}, _MEMBERS, mfj)[1] == "owner_not_in_household"


# --- DB fixtures --------------------------------------------------------------

def _household(name):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()
    _C["households"].append(hid)
    return hid


def _person(full, first, last, household_id):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full, first_name=first, last_name=last,
                                               active=True, household_id=household_id)
                        .returning(people.c.id)).scalar_one()
    _C["people"].append(pid)
    return pid


def _identity(h, person_id, name):
    with engine.begin() as c:
        c.execute(_di.insert().values(identifier_hash=h, primary_person_id=person_id, taxpayer_name=name))
    _C["hashes"].append(h)


def _joint(year, row, tp, sp):
    with engine.begin() as c:
        c.execute(_dcr.insert().values(
            tax_year=year, source_row_number=row, taxpayer_identifier_hash=tp, spouse_identifier_hash=sp,
            taxpayer_first_name="Samuel", taxpayer_last_name="Robinson", spouse_first_name="Alicia",
            spouse_last_name="Robinson", filing_status="MFJ", source_updated_at=func.now(), raw_data={}))


def _doc(person_id, name):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=None, organization_id=None, original_name=name,
            stored_name=f"rw-{_TAG}-{uuid.uuid4().hex}", storage_path="x",
            storage_uri=f"C:\\Clients\\Robinson\\{uuid.uuid4().hex}.pdf", size_bytes=10,
            sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active",
            category="tax_document", tags={"source_system": "Drake", "tax_year": "2025",
                                           "drake_doc_type": "federal_return"}).returning(documents.c.id)
        ).scalar_one()
    _C["documents"].append(did)
    return did


def _robinson_household():
    hid = _household(f"Robinson Household {_TAG}")
    alicia = _person(f"Alicia Robinson {_TAG}", "Alicia", f"Robinson{_TAG}", hid)
    samuel = _person(f"Samuel Robinson {_TAG}", "Samuel", f"Robinson{_TAG}", hid)
    _identity(f"ali{_TAG}", alicia, "Alicia Robinson")
    _identity(f"sam{_TAG}", samuel, "Samuel Robinson")
    for yr, n in ((2023, 1), (2024, 2), (2025, 3)):
        _joint(yr, int(hashlib.sha1(f"{_TAG}{n}".encode()).hexdigest(), 16) % 2_000_000_000,
               f"sam{_TAG}", f"ali{_TAG}")
    # 800 = joint personal return naming both spouses; 799 = business LLC return in Alicia's folder
    d800 = _doc(alicia, f"2025 Tax Return Documents (ROBINSON, SAMUEL M & ALICIA L) {_TAG}.pdf")
    d799 = _doc(alicia, f"2025 Tax Return Documents (INTEGRITY COATINGS LLC) {_TAG}.pdf")
    return hid, alicia, d800, d799


def _doc_owner(did):
    with engine.connect() as c:
        return c.execute(select(documents.c.person_id, documents.c.household_id,
                                documents.c.storage_uri).where(documents.c.id == did)).mappings().one()


# --- preview ------------------------------------------------------------------

def test_preview_reowns_800_excludes_799_business():
    # rename the household surname to plain 'Robinson' so joint_signature matches the filename tokens
    hid = _household(f"Robinson Household {_TAG}")
    alicia = _person(f"Alicia Robinson {_TAG}", "Alicia", "Robinson", hid)
    samuel = _person(f"Samuel Robinson {_TAG}", "Samuel", "Robinson", hid)
    _identity(f"ali{_TAG}", alicia, "Alicia Robinson")
    _identity(f"sam{_TAG}", samuel, "Samuel Robinson")
    for yr, n in ((2023, 1), (2024, 2), (2025, 3)):
        _joint(yr, int(hashlib.sha1(f"{_TAG}{n}".encode()).hexdigest(), 16) % 2_000_000_000,
               f"sam{_TAG}", f"ali{_TAG}")
    d800 = _doc(alicia, f"2025 Tax Return Documents (ROBINSON, SAMUEL M & ALICIA L) {_TAG}.pdf")
    d799 = _doc(alicia, f"2025 Tax Return Documents (INTEGRITY COATINGS LLC) {_TAG}.pdf")

    res = preview()
    row = next((r for r in res["reownable_rows"] if r["document_id"] == d800), None)
    assert row is not None
    assert row["current_person_id"] == alicia and row["proposed_household_id"] == hid
    assert "Households" in row["proposed_destination"] and row["relocation_required"] is True
    excl = {e["document_id"]: e["reason"] for e in res["exclusion_rows"]}
    assert excl.get(d799) == "business_entity_return"           # LLC return excluded
    assert d800 not in excl


# --- guarded apply ------------------------------------------------------------

def test_apply_requires_confirm_and_backup():
    with pytest.raises(ReownershipGuardError):
        apply(confirm=False, backup=_BACKUP)
    with pytest.raises(ReownershipGuardError):
        apply(confirm=True, backup="/nonexistent.dump")


def test_apply_fails_closed_on_count_drift():
    _robinson_household()
    live = preview()["reownable"]
    with pytest.raises(ReownershipGuardError):
        apply(confirm=True, backup=_BACKUP, expect=live + 999)


def test_apply_reowns_800_only_and_is_idempotent():
    hid = _household(f"Robinson Household {_TAG}")
    alicia = _person(f"Alicia Robinson {_TAG}", "Alicia", "Robinson", hid)
    samuel = _person(f"Samuel Robinson {_TAG}", "Samuel", "Robinson", hid)
    _identity(f"ali{_TAG}", alicia, "Alicia Robinson")
    _identity(f"sam{_TAG}", samuel, "Samuel Robinson")
    for yr, n in ((2023, 1), (2024, 2), (2025, 3)):
        _joint(yr, int(hashlib.sha1(f"{_TAG}{n}".encode()).hexdigest(), 16) % 2_000_000_000,
               f"sam{_TAG}", f"ali{_TAG}")
    d800 = _doc(alicia, f"2025 Tax Return Documents (ROBINSON, SAMUEL M & ALICIA L) {_TAG}.pdf")
    d799 = _doc(alicia, f"2025 Tax Return Documents (INTEGRITY COATINGS LLC) {_TAG}.pdf")

    before800 = _doc_owner(d800)
    live = preview()["reownable"]
    res = apply(confirm=True, backup=_BACKUP, expect=live)
    assert res["reowned"] >= 1

    o800 = _doc_owner(d800)
    assert o800["household_id"] == hid and o800["person_id"] is None      # re-owned to household
    assert o800["storage_uri"] == before800["storage_uri"]               # storage_uri unchanged
    o799 = _doc_owner(d799)
    assert o799["person_id"] == alicia and o799["household_id"] is None    # business doc untouched

    # idempotent: 800 is no longer a candidate; re-preview excludes it, re-apply is a no-op for it
    live2 = preview()["reownable"]
    res2 = apply(confirm=True, backup=_BACKUP, expect=live2)
    assert _doc_owner(d800)["household_id"] == hid                        # unchanged
    assert res2["reowned"] == 0 or all(did != d800 for did, _ in res2.get("skipped_conflicts", []))


def test_apply_never_changes_document_count():
    _robinson_household()
    before = _count(documents)
    live = preview()["reownable"]
    apply(confirm=True, backup=_BACKUP, expect=live)
    assert _count(documents) == before          # ownership updated in place; no rows added/removed


def _count(tbl):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(tbl)).scalar_one()
