"""Phase 1 READ-ONLY HIGH-proposal validation: contradiction guards + report + zero mutation."""
import hashlib
import uuid

import pytest
from sqlalchemy import select

from app.db import (
    documents,
    engine,
    people,
    person_source_links,
    relationship_entities,
    source_contacts,
)
from app.services import document_high_validation as hv

_TAG = uuid.uuid4().hex[:8].translate(str.maketrans("0123456789", "abcdefghij")).capitalize()
# Alphabetic + capitalised so names built as f"First {_TAG}" are extractable by the content
# name matcher. A hex tag ("Jennifer a1b2c3d4") is not a name the extractor can see, so these
# fixtures used to reach HIGH on the email alone — the exact rule the safety patch removed.
_A = _TAG.translate(str.maketrans("0123456789", "abcdefghij"))   # alpha-only tag for use inside NAMES
_DOCS: list = []
_PEOPLE: list = []
_SC: list = []
_LINKS: list = []
_ORGS: list = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _LINKS:
            c.execute(person_source_links.delete().where(person_source_links.c.id.in_(_LINKS)))
        if _SC:
            c.execute(source_contacts.delete().where(source_contacts.c.id.in_(_SC)))
        if _DOCS:
            c.execute(documents.delete().where(documents.c.id.in_(_DOCS)))
        if _PEOPLE:
            c.execute(people.delete().where(people.c.id.in_(_PEOPLE)))
        if _ORGS:
            c.execute(relationship_entities.delete().where(relationship_entities.c.id.in_(_ORGS)))
    for lst in (_DOCS, _PEOPLE, _SC, _LINKS, _ORGS):
        lst.clear()


def _person(full_name, email=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, active=True,
                                               contact_type="Client")
                        .returning(people.c.id)).scalar_one()
    _PEOPLE.append(pid)
    if email:
        with engine.begin() as c:
            sid = c.execute(source_contacts.insert().values(
                source_system="TaxDome", source_file="t.zip", source_record_id=uuid.uuid4().hex,
                source_hash=uuid.uuid4().hex, email=email, raw_data={}
            ).returning(source_contacts.c.id)).scalar_one()
            lid = c.execute(person_source_links.insert().values(
                person_id=pid, source_contact_id=sid, match_method="email", confirmed=True
            ).returning(person_source_links.c.id)).scalar_one()
        _SC.append(sid)
        _LINKS.append(lid)
    return pid


def _org(name):
    with engine.begin() as c:
        eid = c.execute(relationship_entities.insert().values(entity_type="business", name=name, active=True)
                        .returning(relationship_entities.c.id)).scalar_one()
    _ORGS.append(eid)
    return eid


def _doc(path, name="f.txt", folder=None):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=None, household_id=None, organization_id=None, original_name=name,
            stored_name=f"hv-{_TAG}-{uuid.uuid4().hex}", storage_path=str(path), storage_uri=str(path),
            size_bytes=10, sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active",
            archived=False, tags={"source_system": "TaxDome Drive", "taxdome_folder": folder or ""}
        ).returning(documents.c.id)).scalar_one()
    _DOCS.append(did)
    return did


def _owner(did):
    with engine.connect() as c:
        return tuple(c.execute(select(documents.c.person_id, documents.c.household_id,
                                      documents.c.organization_id).where(documents.c.id == did)).first())


def _row_for(result, did):
    return next((r for r in result["rows"] if r["document_id"] == did), None)


# --- clean HIGH is eligible; no mutation ----------------------------------------------------------

def test_clean_high_is_eligible_and_read_only(tmp_path):
    email = f"clean-{_TAG}@mail.com"
    pid = _person(f"Cleanperson {_A}", email=email)
    f = tmp_path / "d.txt"
    f.write_text(f"Statement for Cleanperson {_A}, remit to {email}\n")
    did = _doc(f, name="d.txt", folder=f"Cleanperson {_A}")
    result = hv.validate_high_proposals()
    row = _row_for(result, did)
    assert row and row["eligible"] is True and row["contradictions"] == []
    assert row["proposed_entity_id"] == pid and row["confidence"] == "HIGH"
    assert "email" in row["evidence_classes"]
    assert row["identity_provenance"] == "both"          # folder ALSO names the person
    assert _owner(did) == (None, None, None)             # READ-ONLY


# --- contradiction guards each exclude from bulk eligibility --------------------------------------

def test_foreign_strong_identifier_excludes(tmp_path):
    e1, e2 = f"a-{_TAG}@mail.com", f"b-{_TAG}@mail.com"
    p1 = _person(f"Aperson {_A}", email=e1)
    _person(f"Bperson {_A}", email=e2)                  # a DIFFERENT person's email in the same doc
    f = tmp_path / "d.txt"
    # Names Aperson and carries their unique email -> a legitimate HIGH; e2 is then the foreign
    # strong identifier the contradiction guard must catch.
    f.write_text(f"Statement for Aperson {_A}  {e1} and also {e2}\n")
    did = _doc(f, name="d.txt")
    row = _row_for(hv.validate_high_proposals(), did)
    assert row and row["eligible"] is False
    assert "foreign_strong_identifier" in row["contradictions"]
    assert row["proposed_entity_id"] == p1               # still proposed, but not bulk-eligible


def test_folder_identity_conflict_excludes(tmp_path):
    email = f"mary-{_TAG}@mail.com"
    _person(f"Maryperson {_A}", email=email)
    _person(f"Adrianna {_A}")                           # folder names a different canonical person
    f = tmp_path / "d.txt"
    f.write_text(f"Statement for Maryperson {_A}, contact {email}\n")
    did = _doc(f, name="d.txt", folder=f"Adrianna {_A}")
    row = _row_for(hv.validate_high_proposals(), did)
    assert row and row["eligible"] is False
    assert "folder_identity_conflict" in row["contradictions"]


def test_placeholder_candidate_never_reaches_the_high_set(tmp_path):
    """A placeholder canonical record can no longer become a HIGH proposal at all.

    This used to assert that ``placeholder_candidate`` EXCLUDED such a row from bulk confirm — the
    row reached HIGH on the email alone and the contradiction guard caught it afterwards. The owner
    safety rules remove the first half: a placeholder name is deliberately absent from the name index
    (``_placeholder_name``), and HIGH now requires the owner's name plus a unique identifier, so the
    identifier-only route that produced these HIGH rows is gone.

    The guard in ``document_high_validation`` is deliberately left in place as defence in depth; this
    test now pins the stronger property that nothing has to reach it by this route.
    """
    email = f"ph-{_TAG}@mail.com"
    _person("A B", email=email)                           # placeholder canonical name
    f = tmp_path / "d.txt"
    f.write_text(f"Statement for A B\nremit to {email}\n")
    did = _doc(f, name="d.txt")
    assert _row_for(hv.validate_high_proposals(), did) is None   # never enters the HIGH set
    assert _owner(did) == (None, None, None)                     # and nothing was assigned


def test_organization_person_conflict_excludes(tmp_path):
    email = f"op-{_TAG}@mail.com"
    _person(f"Opperson {_A}", email=email)
    _org(f"Widgets {_A} LLC")
    f = tmp_path / "d.txt"
    f.write_text(f"Invoice from Widgets {_A} LLC for Opperson {_A}, remit to {email}\n")
    did = _doc(f, name="d.txt")
    row = _row_for(hv.validate_high_proposals(), did)
    assert row and row["eligible"] is False and "organization_person_conflict" in row["contradictions"]


# --- report shape + native/OCR split -------------------------------------------------------------

def test_report_totals_and_native_ocr_split(tmp_path):
    email = f"split-{_TAG}@mail.com"
    _person(f"Splitperson {_A}", email=email)
    f = tmp_path / "d.txt"
    f.write_text(f"Statement for Splitperson {_A}\nremit to {email}\n")
    did = _doc(f, name="d.txt")
    result = hv.validate_high_proposals()
    assert result["high_total"] == result["eligible"] + result["excluded"]
    assert result["native_high"] + result["ocr_high"] == result["high_total"]
    row = _row_for(result, did)
    assert row["extraction_class"] == "native"           # plaintext = native, not OCR
    assert set(result["reason_counts"]) <= set(hv.CONTRADICTION_CLASSES)
