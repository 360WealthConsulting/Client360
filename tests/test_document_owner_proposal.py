"""READ-ONLY content-based owner proposal for unassigned documents.

Pure analysis tests (text -> ranked proposal, no DB) cover: exact identity, single-field, unique-name,
duplicate-name ambiguity, mixed-owner split, institution/payor false-positive protection, household
joint, business, and no-match. Integration tests cover extraction + eligibility + no mutation + UI.
"""
import hashlib
import uuid

import pytest
from sqlalchemy import select

from app.db import documents, engine, people
from app.services import document_owner_proposal as dop
from app.services.document_owner_proposal import analyze_identity

_TAG = uuid.uuid4().hex[:8]
_DOCS: list = []
_PEOPLE: list = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _DOCS:
            c.execute(documents.delete().where(documents.c.id.in_(_DOCS)))
        if _PEOPLE:
            c.execute(people.delete().where(people.c.id.in_(_PEOPLE)))
    _DOCS.clear()
    _PEOPLE.clear()


def _idx():
    return {
        "email": {"jl@x.com": {2421}},
        "phone": {"4403826802": {2421}},
        "name": {"jennifer suplita": [2421], "mary hardy": [7430], "adrianna lomasney": [5284],
                 "john smith": [100, 101], "deborah mcdaniel": [5338], "harold mcdaniel": [7530]},
        "pid": {2421: {"name": "Jennifer Suplita"}, 7430: {"name": "MARY HARDY"},
                5284: {"name": "Adrianna Lomasney"}, 100: {"name": "John Smith"},
                101: {"name": "John Smith"}, 5338: {"name": "Deborah McDaniel"},
                7530: {"name": "Harold McDaniel"}},
        "members": {113: {5338, 7530}},
        "hh_name": {113: "Mcdaniel Household"},
        "biz": {"widgets llc": (9, "Widgets LLC")},
        "inst": {"wells fargo", "liberty university"},
    }


# --- pure analysis -----------------------------------------------------------------------------

def test_exact_identity_name_plus_email_is_high():
    r = analyze_identity("Taxpayer: Jennifer Suplita\nEmail: jl@x.com", "f.pdf", "F", _idx())
    assert r["confidence"] == "HIGH_CONFIDENCE"
    assert (r["proposed_entity_type"], r["proposed_entity_id"]) == ("person", 2421)


def test_single_email_is_review_not_high():
    r = analyze_identity("remittance to jl@x.com", "f.pdf", "F", _idx())
    assert r["confidence"] == "REVIEW_RECOMMENDED"
    assert r["proposed_entity_id"] == 2421


def test_unique_name_only_is_review_not_high():
    r = analyze_identity("2021 Form 1040 for MARY HARDY", "f.pdf", "F", _idx())
    assert r["confidence"] == "REVIEW_RECOMMENDED"        # name alone never HIGH
    assert r["proposed_entity_id"] == 7430


def test_duplicate_name_is_ambiguous():
    r = analyze_identity("Prepared for John Smith", "f.pdf", "F", _idx())
    assert r["confidence"] == "AMBIGUOUS"
    assert r["proposed_entity_id"] is None
    assert {c["person_id"] for c in r["competing"]} == {100, 101}


def test_mixed_owner_content_splits_by_document():
    idx = _idx()
    only_adrianna = analyze_identity("Client: Adrianna Lomasney", "a.pdf", "Adrianna Hardy", idx)
    only_mary = analyze_identity("Recipient MARY HARDY", "b.pdf", "Adrianna Hardy", idx)
    both = analyze_identity("Adrianna Lomasney and MARY HARDY", "c.pdf", "Adrianna Hardy", idx)
    assert only_adrianna["proposed_entity_id"] == 5284
    assert only_mary["proposed_entity_id"] == 7430
    assert both["confidence"] == "AMBIGUOUS"              # two distinct, non-household people -> ambiguous


def test_institution_name_alone_is_never_owner():
    r = analyze_identity("Wells Fargo mortgage statement 1098", "f.pdf", "F", _idx())
    assert r["confidence"] == "NO_MATCH" and r["proposed_entity_id"] is None
    assert "wells fargo" in r["extracted"]["institutions"]


def test_institution_is_context_when_a_real_owner_is_present():
    r = analyze_identity("Wells Fargo 1099 for Jennifer Suplita jl@x.com", "f.pdf", "F", _idx())
    assert (r["proposed_entity_type"], r["proposed_entity_id"]) == ("person", 2421)
    assert "wells fargo" in r["extracted"]["institutions"]   # recorded, but not the owner


def test_two_household_members_named_is_household_high():
    r = analyze_identity("Joint return: Deborah McDaniel and Harold McDaniel", "f.pdf", "F", _idx())
    assert (r["proposed_entity_type"], r["proposed_entity_id"]) == ("household", 113)
    assert r["confidence"] == "HIGH_CONFIDENCE"


def test_business_legal_name_proposes_organization():
    r = analyze_identity("Invoice from Widgets LLC", "f.pdf", "F", _idx())
    assert (r["proposed_entity_type"], r["proposed_entity_id"]) == ("organization", 9)
    assert r["confidence"] == "REVIEW_RECOMMENDED"


def test_no_identity_is_no_match():
    r = analyze_identity("miscellaneous notes with no identity", "f.pdf", "F", _idx())
    assert r["confidence"] == "NO_MATCH" and r["proposed_entity_id"] is None


# --- extraction + eligibility + no mutation + reject ------------------------------------------

_UNIQUE_NAME = "Zebulon Quibbleworth"   # alpha-only + very unlikely to exist in the seeded test DB


def _person_with(email, full_name=_UNIQUE_NAME):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, active=True,
                                               primary_email=email).returning(people.c.id)).scalar_one()
    _PEOPLE.append(pid)
    return pid


def _doc(path=None, *, person_id=None, household_id=None, organization_id=None, name="f.txt"):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=household_id, organization_id=organization_id,
            original_name=name, stored_name=f"op-{_TAG}-{uuid.uuid4().hex}",
            storage_path=str(path) if path else "x", storage_uri=str(path) if path else "C:\\x.txt",
            size_bytes=10, sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active",
            archived=False, tags={"source_system": "TaxDome Drive", "taxdome_folder": f"F-{_TAG}"}
        ).returning(documents.c.id)).scalar_one()
    _DOCS.append(did)
    return did


def _owner(did):
    with engine.connect() as c:
        r = c.execute(select(documents.c.person_id, documents.c.household_id, documents.c.organization_id)
                      .where(documents.c.id == did)).mappings().first()
    return (r["person_id"], r["household_id"], r["organization_id"])


def test_extract_plaintext_and_propose_person_no_mutation(tmp_path):
    email = f"zeta-{_TAG}@example.com"
    pid = _person_with(email)
    f = tmp_path / "letter.txt"
    f.write_text(f"Taxpayer: {_UNIQUE_NAME}\nEmail: {email}\n")
    did = _doc(f, name="letter.txt")
    r = dop.propose_document_owner(did)
    assert r["eligible"] is True and r["extraction_method"] == "plaintext"
    assert r["proposed_entity_type"] == "person" and r["proposed_entity_id"] == pid
    assert r["confidence"] == "HIGH_CONFIDENCE"          # name + email corroboration
    assert _owner(did) == (None, None, None)             # READ-ONLY: nothing assigned


def test_extract_excel_text(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Client", f"unique-{_TAG}@mail.com"])
    f = tmp_path / "book.xlsx"; wb.save(f)
    with engine.connect() as conn:
        text, method = dop.extract_document_text(conn, {"id": -1, "original_name": "book.xlsx"}, f)
    assert method == "excel" and f"unique-{_TAG}@mail.com" in text


def test_reject_document_is_not_eligible(monkeypatch):
    did = _doc(name="x.txt")
    monkeypatch.setattr(dop, "PERMANENT_REJECT_DOCUMENT_IDS", frozenset({did}))
    r = dop.propose_document_owner(did)
    assert r["eligible"] is False and r["reason"] == "permanent_reject"


def test_already_owned_document_is_not_eligible():
    did = _doc(person_id=_person_with(f"owned-{_TAG}@e.com"), name="x.txt")
    r = dop.propose_document_owner(did)
    assert r["eligible"] is False and r["reason"] == "already_owned"


# --- UI --------------------------------------------------------------------------------------

def test_review_template_shows_proposal_confidence_and_evidence():
    from app.routes.admin import templates
    from app.security.models import Principal
    p = Principal(1, "a@e.com", "Admin", frozenset({"client.write"}))
    doc = {"id": 457, "name": "a.pdf", "doc_type": None, "year": None, "current_owner": "Unassigned (NULL)",
           "view_url": "/documents/457/download?inline=1", "download_url": "/documents/457/download",
           "proposal": {"proposed_entity_type": "person", "proposed_entity_id": 7430,
                        "proposed_entity_name": "MARY HARDY", "confidence": "HIGH_CONFIDENCE",
                        "evidence": ["email m@e.com maps to #7430", "name 'mary hardy' matches #7430"],
                        "competing": [], "extraction_method": "pdf_text"}}
    html = templates.get_template("admin/unassigned_review.html").render(
        request=None, principal=p, folder="Adrianna Hardy", eligible_docs=[doc],
        already_owned_docs=[], excluded_docs=[], candidates=[], household_candidates=[], org_candidates=[])
    assert "PROPOSED OWNER (from document content)" in html
    assert "MARY HARDY" in html and "HIGH_CONFIDENCE" in html
    assert "email m@e.com maps to #7430" in html
    assert "Preview → proposed owner: MARY HARDY (#7430)" in html
