"""Coverage for the READ-ONLY resolution evidence assembler (PR-2).

Proves evidence assembly for person / household / business candidates; that ambiguous and shared-identity
subjects preserve uncertainty (never collapsed to certainty); truly-absent subjects; that current reusable
resolution knowledge is surfaced while superseded / non-reusable ledger decisions are NOT returned as
reusable; and that assembling performs ZERO writes.
"""
import hashlib
import uuid

import pytest
from sqlalchemy import func, select

from app.db import (
    documents,
    engine,
    households,
    metadata,
    people,
    relationship_entities,
    source_contacts,
)
from app.importers.taxdome_drive import _name_key
from app.services.migration.evidence_assembler import assemble_folder_subject, build_context
from app.services.resolution_knowledge import record_decision

_TAG = uuid.uuid4().hex[:8]
_SYS = "TaxDome Drive"
_C = {"documents": [], "source_contacts": [], "people": [], "households": [],
      "relationship_entities": [], "folder_resolution_decisions": []}
_frd = metadata.tables["folder_resolution_decisions"]
_person_source_links = metadata.tables["person_source_links"]


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        c.execute(_frd.update().where(_frd.c.subject_system == _SYS.casefold())
                  .values(superseded_by=None))
        c.execute(_frd.delete().where(_frd.c.subject_system == _SYS.casefold()))
        for tbl, key in ((documents, "documents"), (source_contacts, "source_contacts"),
                         (relationship_entities, "relationship_entities"), (people, "people"),
                         (households, "households")):
            if _C[key]:
                c.execute(tbl.delete().where(tbl.c.id.in_(_C[key])))
    for k in _C:
        _C[k].clear()


def _person(full_name, *, email=None, phone=None, household_id=None):
    first, last = (full_name.split(" ", 1) + [""])[:2]
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            full_name=full_name, first_name=first, last_name=last, primary_email=email,
            normalized_email=email, primary_phone=phone, normalized_phone=phone, active=True,
            household_id=household_id).returning(people.c.id)).scalar_one()
    _C["people"].append(pid)
    return pid


def _household(name):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()
    _C["households"].append(hid)
    return hid


def _business(name):
    with engine.begin() as c:
        eid = c.execute(relationship_entities.insert().values(
            entity_type="business", name=name, active=True).returning(relationship_entities.c.id)
        ).scalar_one()
    _C["relationship_entities"].append(eid)
    return eid


def _sc(full_name, *, system="Wealthbox", email=None, phone=None, raw=None, srid=None):
    with engine.begin() as c:
        sid = c.execute(source_contacts.insert().values(
            source_system=system, source_file="test.csv", source_hash=uuid.uuid4().hex,
            source_record_id=srid, full_name=full_name, normalized_email=email, normalized_phone=phone,
            raw_data=raw or {}).returning(source_contacts.c.id)).scalar_one()
    _C["source_contacts"].append(sid)
    return sid


def _doc(folder, original_name="doc.pdf"):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=None, household_id=None, organization_id=None, original_name=original_name,
            stored_name=f"ev-{_TAG}-{uuid.uuid4().hex}", storage_path="x",
            storage_uri="C:\\legacy\\" + original_name, size_bytes=10,
            sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active",
            tags={"source_system": _SYS, "taxdome_folder": folder}).returning(documents.c.id)).scalar_one()
    _C["documents"].append(did)
    return did


def _count(tbl):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(tbl)).scalar_one()


# --- candidate assembly -------------------------------------------------------

def test_person_candidate_and_deterministic_outcome():
    name = f"Evan Assembler {_TAG}"
    pid = _person(name, email=f"evan{_TAG}@x.com")
    sid = _sc(name, system="Wealthbox", email=f"evan{_TAG}@x.com")
    _doc(name, "1040.pdf")
    b = assemble_folder_subject(name, context=build_context())

    assert b["document_count"] == 1 and b["documents"][0]["original_name"] == "1040.pdf"
    assert any(c["person_id"] == pid and c["deterministic"] for c in b["person_candidates"])
    assert any(c["source_contact_id"] == sid and c["basis"] == "exact_name"
               for c in b["source_contact_candidates"])
    assert "Wealthbox" in b["provenance"]
    assert f"evan{_TAG}@x.com" in b["identifiers"]["emails"]
    # folder name uniquely matches a canonical person -> deterministic outcome via the linkage resolver
    assert b["deterministic_outcome"] and b["deterministic_outcome"]["entity_type"] == "person"
    assert b["deterministic_outcome"]["entity_id"] == pid and b["held_reason"] is None


def test_household_candidate():
    name = f"Smithfam Household {_TAG}"
    hid = _household(name)
    _doc(name)
    b = assemble_folder_subject(name, context=build_context())
    assert any(c["household_id"] == hid and c["basis"] == "exact_name" and c["deterministic"]
               for c in b["household_candidates"])


def test_business_candidate_and_outcome():
    name = f"Star City Heating {_TAG}"
    eid = _business(name)
    _doc(name)
    b = assemble_folder_subject(name, context=build_context())
    assert any(c["entity_id"] == eid and c["deterministic"] for c in b["business_candidates"])
    assert b["deterministic_outcome"] and b["deterministic_outcome"]["entity_type"] == "relationship_entity"
    assert b["deterministic_outcome"]["entity_id"] == eid


def test_ambiguous_subject_surfaces_conflict_not_certainty():
    name = f"Dupe Person {_TAG}"
    _person(name)
    _person(name)                                    # duplicate canonical people -> ambiguous
    _doc(name)
    b = assemble_folder_subject(name, context=build_context())
    assert b["deterministic_outcome"] is None and b["held_reason"]        # not collapsed to a match
    assert b["evidence_flags"]["has_conflicting_evidence"] is True
    assert len([c for c in b["person_candidates"] if c["deterministic"]]) >= 2   # both retained


def test_shared_identifier_not_promoted_to_certainty():
    email = f"shared{_TAG}@x.com"
    sid1 = _sc(f"Spouse One {_TAG}", email=email)
    _sc(f"Spouse Two {_TAG}", email=email)           # same email shared across unlinked contacts
    _doc(f"Spouse One {_TAG}")
    b = assemble_folder_subject(f"Spouse One {_TAG}", context=build_context())
    shared = [c for c in b["source_contact_candidates"] if c["source_contact_id"] == sid1]
    assert shared and shared[0]["basis"] == "shared_identifier" and shared[0]["deterministic"] is False
    assert b["evidence_flags"]["has_shared_identifier"] is True


def test_truly_absent_subject():
    name = f"Nobody Absent {_TAG}"
    _doc(name)
    b = assemble_folder_subject(name, context=build_context())
    assert not b["person_candidates"] and not b["household_candidates"]
    assert not b["business_candidates"] and not b["source_contact_candidates"]
    assert b["deterministic_outcome"] is None and b["held_reason"]
    assert b["evidence_flags"]["no_candidates"] is True and b["confidence"] == "none"


# --- durable resolution knowledge ---------------------------------------------

def test_reusable_resolution_knowledge_surfaced():
    name = f"Known Client {_TAG}"
    pid = _person(name)
    _doc(name)
    record_decision(subject_system=_SYS, subject_type="folder", subject_key=_name_key(name),
                    display_name=name, decision="link_person", resulting_entity_type="person",
                    resulting_entity_id=pid, reviewed_by="Tester")
    b = assemble_folder_subject(name, context=build_context())
    assert b["reusable_resolution"] and b["reusable_resolution"]["decision"] == "link_person"
    assert b["reusable_resolution"]["resulting_entity_id"] == pid
    assert b["current_resolution"]["decision"] == "link_person"
    assert b["resolution_history_count"] == 1


def test_superseded_and_non_reusable_not_treated_as_reusable():
    name = f"Corrected Client {_TAG}"
    pid = _person(name)
    _doc(name)
    record_decision(subject_system=_SYS, subject_type="folder", subject_key=_name_key(name),
                    display_name=name, decision="link_person", resulting_entity_type="person",
                    resulting_entity_id=pid)
    record_decision(subject_system=_SYS, subject_type="folder", subject_key=_name_key(name),
                    display_name=name, decision="reject", supersede=True, reviewed_by="Tester")
    b = assemble_folder_subject(name, context=build_context())
    assert b["current_resolution"]["decision"] == "reject"
    assert b["reusable_resolution"] is None                       # rejection is not reusable knowledge
    assert b["resolution_history_count"] == 2                     # superseded row retained


# --- read-only guarantee ------------------------------------------------------

def test_assembler_performs_zero_writes():
    name = f"Zero Writes {_TAG}"
    _person(name, email=f"zw{_TAG}@x.com")
    _sc(name, email=f"zw{_TAG}@x.com")
    _doc(name)
    ctx = build_context()
    exceptions = metadata.tables.get("exceptions")
    before = {t.name: _count(t) for t in
              (people, source_contacts, documents, _frd, _person_source_links)}
    before_exc = _count(exceptions) if exceptions is not None else 0

    assemble_folder_subject(name, context=ctx)
    assemble_folder_subject(name, context=ctx)                    # twice — still no writes

    after = {t.name: _count(t) for t in
             (people, source_contacts, documents, _frd, _person_source_links)}
    assert after == before
    if exceptions is not None:
        assert _count(exceptions) == before_exc
