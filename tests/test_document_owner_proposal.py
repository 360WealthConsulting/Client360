"""READ-ONLY content-based owner proposal for unassigned documents.

Pure analysis tests (text -> ranked proposal, no DB) cover extraction + deterministic scoring: email,
phone, exact name, first/last + 'Last, First', address corroboration, common-surname weakness,
disambiguation by a stronger identifier, source-folder-vs-content, household joint, business, no-match,
and SSN masking. Integration tests cover extraction + eligibility + no mutation + UI.
"""
import hashlib
import json
import uuid

import pytest
from sqlalchemy import select

from app.db import documents, engine, people, person_source_links, source_contacts
from app.services import document_owner_proposal as dop
from app.services.document_owner_proposal import analyze_identity

_TAG = uuid.uuid4().hex[:8]
_DOCS: list = []
_PEOPLE: list = []
_SC: list = []
_LINKS: list = []


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
    _DOCS.clear()
    _PEOPLE.clear()
    _SC.clear()
    _LINKS.clear()


def _idx():
    return {
        "email": {"jl@x.com": {2421}, "john@x.com": {100}},
        "phone": {"4403826802": {2421}},
        "name": {"jennifer suplita": [2421], "mary hardy": [7430], "adrianna lomasney": [5284],
                 "john smith": [100, 101], "deborah mcdaniel": [5338], "harold mcdaniel": [7530]},
        "first_last": {("mary", "hardy"): [7430], ("jennifer", "suplita"): [2421],
                       ("john", "smith"): [100, 101], ("deborah", "mcdaniel"): [5338],
                       ("harold", "mcdaniel"): [7530], ("adrianna", "lomasney"): [5284]},
        "pid": {
            2421: {"name": "Jennifer Suplita", "email": "jl@x.com", "phone": "4403826802",
                   "zips": set(), "streets": set()},
            7430: {"name": "MARY HARDY", "zips": {"23112"}, "streets": {"100 oak st"}},
            5284: {"name": "Adrianna Lomasney", "zips": set(), "streets": set()},
            100: {"name": "John Smith", "email": "john@x.com", "zips": set(), "streets": set()},
            101: {"name": "John Smith", "zips": set(), "streets": set()},
            5338: {"name": "Deborah McDaniel", "zips": set(), "streets": set()},
            7530: {"name": "Harold McDaniel", "zips": set(), "streets": set()},
        },
        "members": {113: {5338, 7530}},
        "hh_name": {113: "Mcdaniel Household"},
        "biz": {"widgets llc": (9, "Widgets LLC")},
        "inst": {"wells fargo", "liberty university"},
    }


# --- scoring / confidence ---------------------------------------------------------------------

def test_exact_name_plus_email_is_high():
    r = analyze_identity("Taxpayer: Jennifer Suplita\nEmail: jl@x.com", "f.pdf", "F", _idx())
    assert r["confidence"] == "HIGH" and (r["proposed_entity_type"], r["proposed_entity_id"]) == ("person", 2421)


def test_email_alone_is_high():
    r = analyze_identity("remittance to jl@x.com", "f.pdf", "F", _idx())
    assert r["confidence"] == "HIGH" and r["proposed_entity_id"] == 2421   # exact email = very strong


def test_phone_alone_is_high():
    r = analyze_identity("Call 440-382-6802 regarding your return", "f.pdf", "F", _idx())
    assert r["confidence"] == "HIGH" and r["proposed_entity_id"] == 2421
    assert any("phone ending 6802" in e for e in r["evidence"])


def test_name_plus_address_zip_is_high():
    r = analyze_identity("Recipient MARY HARDY, mailing ZIP 23112", "f.pdf", "F", _idx())
    assert r["confidence"] == "HIGH" and r["proposed_entity_id"] == 7430
    assert any("address/ZIP matched" in e for e in r["evidence"])


def test_exact_full_name_alone_is_medium():
    r = analyze_identity("2021 Form 1040 for Jennifer Suplita", "f.pdf", "F", _idx())
    assert r["confidence"] == "MEDIUM" and r["proposed_entity_id"] == 2421   # name alone never HIGH


def test_first_last_matches_name_with_middle():
    r = analyze_identity("Prepared for MARY A HARDY", "f.pdf", "F", _idx())
    assert r["proposed_entity_id"] == 7430 and r["confidence"] == "MEDIUM"


def test_last_comma_first_format_matches():
    r = analyze_identity("HARDY, MARY  2021 1095-A", "f.pdf", "F", _idx())
    assert r["proposed_entity_id"] == 7430


def test_common_surname_alone_is_not_high():
    r = analyze_identity("Hardy 2021 tax return", "f.pdf", "F", _idx())   # single surname token only
    assert r["confidence"] == "NO_MATCH" and r["proposed_entity_id"] is None


def test_duplicate_name_is_ambiguous():
    r = analyze_identity("Prepared for John Smith", "f.pdf", "F", _idx())
    assert r["confidence"] == "AMBIGUOUS" and r["proposed_entity_id"] is None
    assert {c["person_id"] for c in r["best_candidates"]} == {100, 101}


def test_duplicate_name_disambiguated_by_email():
    r = analyze_identity("John Smith  john@x.com", "f.pdf", "F", _idx())
    assert r["confidence"] == "HIGH" and r["proposed_entity_id"] == 100   # email breaks the tie


def test_source_folder_does_not_override_content():
    # document sits in the "Adrianna Hardy" folder but its content names Mary -> content wins
    r = analyze_identity("Recipient MARY HARDY, ZIP 23112", "x.pdf", "Adrianna Hardy", _idx())
    assert r["proposed_entity_id"] == 7430


def test_mixed_owner_content_splits_by_document():
    idx = _idx()
    assert analyze_identity("Client: Adrianna Lomasney", "a.pdf", "Adrianna Hardy", idx)["proposed_entity_id"] == 5284
    assert analyze_identity("Recipient MARY HARDY", "b.pdf", "Adrianna Hardy", idx)["proposed_entity_id"] == 7430
    both = analyze_identity("Adrianna Lomasney and MARY HARDY", "c.pdf", "Adrianna Hardy", idx)
    assert both["confidence"] == "AMBIGUOUS"


def test_institution_alone_is_never_owner():
    r = analyze_identity("Wells Fargo mortgage statement 1098", "f.pdf", "F", _idx())
    assert r["confidence"] == "NO_MATCH" and r["proposed_entity_id"] is None
    assert "wells fargo" in r["extracted"]["institutions"]


def test_institution_is_context_when_real_owner_present():
    r = analyze_identity("Wells Fargo 1099 for Jennifer Suplita jl@x.com", "f.pdf", "F", _idx())
    assert (r["proposed_entity_type"], r["proposed_entity_id"]) == ("person", 2421)
    assert "wells fargo" in r["extracted"]["institutions"]


def test_two_household_members_named_is_household_high():
    r = analyze_identity("Joint return: Deborah McDaniel and Harold McDaniel", "f.pdf", "F", _idx())
    assert (r["proposed_entity_type"], r["proposed_entity_id"]) == ("household", 113)
    assert r["confidence"] == "HIGH"


def test_business_legal_name_proposes_organization():
    r = analyze_identity("Invoice from Widgets LLC", "f.pdf", "F", _idx())
    assert (r["proposed_entity_type"], r["proposed_entity_id"]) == ("organization", 9)
    assert r["confidence"] == "MEDIUM"                       # no address -> MEDIUM, not HIGH


def test_no_identity_is_no_match():
    r = analyze_identity("miscellaneous notes with no identity", "f.pdf", "F", _idx())
    assert r["confidence"] == "NO_MATCH" and r["proposed_entity_id"] is None


def test_full_ssn_never_appears_in_evidence_or_extracted():
    r = analyze_identity("SSN 123-45-6789 for Jennifer Suplita jl@x.com", "f.pdf", "F", _idx())
    blob = json.dumps(r)
    assert "123-45-6789" not in blob and "123456789" not in blob   # full SSN never leaks
    assert "***-**-6789" in r["extracted"]["ssn_last4"]            # last four only, masked


# --- case-insensitive name detection (the exact #459 structural production defect) -----------------

# Real extracted 1095-A text: the taxpayer name is LOWERCASE and glued to a date ("2022mary hardy").
_PROD_1095A = (
    "DEPARTMENT OF HEALTH AND HUMAN SERVICES\n"
    "January 4, 2022mary hardy\n316 Tilden St\nApt-C\nRichmond, VA 23221-2342\n"
    "Dear mary hardy:\nBecause you and/or members of your household had Health Insurance "
    "Marketplace coverage, the Internal Revenue Service ...\n")


def test_lowercase_pdf_name_becomes_candidate_and_folder_does_not_override():
    # exact structural case; the person's address is NOT in this index -> unique full name alone = MEDIUM
    r = analyze_identity(_PROD_1095A, "Form1095a_2021.pdf", "Adrianna Hardy", _idx())
    assert (r["proposed_entity_type"], r["proposed_entity_id"], r["confidence"]) == ("person", 7430, "MEDIUM")
    assert any("MARY HARDY" in e for e in r["evidence"])          # detected despite lowercase text


def test_lowercase_pdf_name_plus_matching_address_is_high():
    idx = _idx()
    idx["pid"][7430]["zips"] = {"23221"}
    idx["pid"][7430]["streets"] = {"316 tilden st"}
    r = analyze_identity(_PROD_1095A, "Form1095a_2021.pdf", "Adrianna Hardy", idx)
    assert (r["proposed_entity_id"], r["confidence"]) == (7430, "HIGH")
    assert any("address/ZIP matched" in e for e in r["evidence"])


@pytest.mark.parametrize("form", ["MARY HARDY", "Mary Hardy", "mary hardy", "Mary A Hardy", "HARDY, MARY"])
def test_name_case_and_format_variants_all_match(form):
    r = analyze_identity(f"Dear {form}, regarding your 2021 filing.", "x.pdf", "Adrianna Hardy", _idx())
    assert r["proposed_entity_id"] == 7430


def test_lowercase_duplicate_name_stays_ambiguous():
    r = analyze_identity("dear john smith, regarding your account", "x.pdf", "F", _idx())
    assert r["confidence"] == "AMBIGUOUS" and r["proposed_entity_id"] is None


def test_generic_lowercase_boilerplate_creates_no_owner():
    txt = ("affordable care act application id and human services covered individual "
           "primary applicant policy holder for the marketplace")
    r = analyze_identity(txt, "notice.pdf", "Adrianna Hardy", _idx())
    assert r["confidence"] == "NO_MATCH" and r["proposed_entity_id"] is None


def test_transaction_workbook_without_identity_is_no_match():
    txt = "ADOBE ID CREATIVE CLD\nADOBE PHOTOGPHY PLAN\nLATER.COM\nTotal 42.00"
    r = analyze_identity(txt, "Expenses.xlsx", "Adrianna Hardy", _idx())
    assert r["confidence"] == "NO_MATCH" and r["proposed_entity_id"] is None


def test_numeric_form_ids_not_read_as_phone():
    # 10-digit runs with an invalid area/exchange (leading 0/1) must not be treated as phone numbers
    r = analyze_identity("Application ID 1019456998 and reference 1112223333", "x.pdf", "F", _idx())
    assert r["extracted"]["phones"] == []


# --- extraction + eligibility + no mutation --------------------------------------------------

_UNIQUE_NAME = "Zebulon Quibbleworth"   # alpha-only + very unlikely to exist in the seeded test DB


def _person_with(email, full_name=_UNIQUE_NAME):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, active=True,
                                               primary_email=email).returning(people.c.id)).scalar_one()
    _PEOPLE.append(pid)
    return pid


def _doc(path=None, *, person_id=None, name="f.txt"):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=None, organization_id=None, original_name=name,
            stored_name=f"op-{_TAG}-{uuid.uuid4().hex}", storage_path=str(path) if path else "x",
            storage_uri=str(path) if path else "C:\\x.txt", size_bytes=10,
            sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active", archived=False,
            tags={"source_system": "TaxDome Drive", "taxdome_folder": f"F-{_TAG}"}
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
    assert (r["proposed_entity_type"], r["proposed_entity_id"], r["confidence"]) == ("person", pid, "HIGH")
    assert _owner(did) == (None, None, None)             # READ-ONLY: nothing assigned


def test_extract_excel_cells(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Client", f"unique-{_TAG}@mail.com"])
    f = tmp_path / "book.xlsx"; wb.save(f)
    with engine.connect() as conn:
        text, method = dop.extract_document_text(conn, {"id": -1, "original_name": "book.xlsx"}, f)
    assert method == "excel" and f"unique-{_TAG}@mail.com" in text


def test_empty_excel_is_no_confident_match(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook(); wb.active.append(["Total", 42])
    f = tmp_path / "expenses.xlsx"; wb.save(f)
    did = _doc(f, name="expenses.xlsx")
    r = dop.propose_document_owner(did)
    assert r["eligible"] is True and r["confidence"] == "NO_MATCH"


def test_reject_document_is_not_eligible(monkeypatch):
    did = _doc(name="x.txt")
    monkeypatch.setattr(dop, "PERMANENT_REJECT_DOCUMENT_IDS", frozenset({did}))
    r = dop.propose_document_owner(did)
    assert r["eligible"] is False and r["reason"] == "permanent_reject"


def test_already_owned_document_is_not_eligible():
    did = _doc(person_id=_person_with(f"owned-{_TAG}@e.com"), name="x.txt")
    r = dop.propose_document_owner(did)
    assert r["eligible"] is False and r["reason"] == "already_owned"


# --- source-contact enrichment (canonical contact fields are largely NULL in this data model) -------

def _person_no_contact(full_name):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, active=True)
                        .returning(people.c.id)).scalar_one()
    _PEOPLE.append(pid)
    return pid


def _source_contact_link(pid, *, email=None, phone=None, raw=None):
    with engine.begin() as c:
        sid = c.execute(source_contacts.insert().values(
            source_system="TaxDome", source_file="t.zip", source_record_id=uuid.uuid4().hex,
            source_hash=uuid.uuid4().hex, email=email, phone=phone, raw_data=(raw or {})
        ).returning(source_contacts.c.id)).scalar_one()
        lid = c.execute(person_source_links.insert().values(
            person_id=pid, source_contact_id=sid, match_method="email", confirmed=True
        ).returning(person_source_links.c.id)).scalar_one()
    _SC.append(sid)
    _LINKS.append(lid)


def test_build_indexes_reads_email_phone_from_linked_source_contacts():
    email = f"srconly-{_TAG}@mail.com"
    pid = _person_no_contact(f"Quibbleworth {_TAG}")           # NULL canonical email/phone
    _source_contact_link(pid, email=email, phone="804-218-9034",
                         raw={"home_email": f"home-{_TAG}@mail.com"})
    with engine.connect() as conn:
        idx = dop.build_match_indexes(conn)
    assert pid in idx["email"].get(email, set())               # structured source email indexed
    assert pid in idx["email"].get(f"home-{_TAG}@mail.com", set())   # raw_data home_email indexed
    assert pid in idx["phone"].get("8042189034", set())        # source phone indexed


def test_content_matches_person_via_source_contact_email_outside_folder(tmp_path):
    # Person has NO canonical contact fields; the only email lives on a linked source_contact. A document
    # whose content carries that email must still propose this person (folder is irrelevant here).
    email = f"content-{_TAG}@mail.com"
    pid = _person_no_contact(f"Zephyrina {_TAG}")
    _source_contact_link(pid, email=email)
    f = tmp_path / "notice.txt"
    f.write_text(f"Please remit to {email} regarding the 2021 filing.\n")
    did = _doc(f, name="notice.txt")
    r = dop.propose_document_owner(did)
    assert (r["proposed_entity_type"], r["proposed_entity_id"], r["confidence"]) == ("person", pid, "HIGH")
    assert any("matched" in e for e in r["evidence"])
    assert _owner(did) == (None, None, None)                   # still no mutation


def test_content_matches_person_via_source_contact_phone(tmp_path):
    pid = _person_no_contact(f"Thaddeus {_TAG}")
    _source_contact_link(pid, phone="(804) 218-9034")
    f = tmp_path / "call.txt"
    f.write_text("Contact number on file: 804-218-9034\n")
    did = _doc(f, name="call.txt")
    r = dop.propose_document_owner(did)
    assert (r["proposed_entity_type"], r["proposed_entity_id"], r["confidence"]) == ("person", pid, "HIGH")


# --- candidate-quality guard (placeholder canonical people) ----------------------------------------

def test_placeholder_name_helper():
    assert dop._placeholder_name("A B") and dop._placeholder_name("T T") and dop._placeholder_name("D F")
    assert dop._placeholder_name("") and dop._placeholder_name(None)
    assert not dop._placeholder_name("Al Vo") and not dop._placeholder_name("Ed Ng")
    assert not dop._placeholder_name("J R Smith")          # a real surname token present


def test_placeholder_person_excluded_from_name_index_but_kept_in_pid():
    pid = _person_no_contact("A B")                        # placeholder canonical record, no contact info
    with engine.connect() as conn:
        idx = dop.build_match_indexes(conn)
    assert pid not in idx["name"].get("a b", [])
    assert pid not in idx["first_last"].get(("a", "b"), [])
    assert pid in idx["pid"]                                # still known (not deleted)


def test_placeholder_name_produces_no_owner_from_content(tmp_path):
    _person_no_contact("C D")
    f = tmp_path / "doc.txt"
    f.write_text("Prepared for C D on 2021 return\n")
    did = _doc(f, name="doc.txt")
    r = dop.propose_document_owner(did)
    assert r["proposed_entity_id"] is None and r["confidence"] in ("NO_MATCH", "AMBIGUOUS")


def test_legitimate_short_name_still_matches(tmp_path):
    pid = _person_no_contact(f"Al Vo{_TAG[:3]}")           # genuine short name (2+ char tokens)
    with engine.connect() as conn:
        idx = dop.build_match_indexes(conn)
    assert pid in idx["first_last"].get(("al", f"vo{_TAG[:3]}"), [])


def test_placeholder_person_still_matchable_by_email(tmp_path):
    email = f"ab-{_TAG}@mail.com"
    pid = _person_no_contact("A B")                        # placeholder name, but a real linked email
    _source_contact_link(pid, email=email)
    f = tmp_path / "d.txt"
    f.write_text(f"remit to {email}\n")
    did = _doc(f, name="d.txt")
    r = dop.propose_document_owner(did)
    assert (r["proposed_entity_id"], r["confidence"]) == (pid, "HIGH")   # email is strong regardless


# --- OCR / image extraction fallback --------------------------------------------------------------

def test_heic_is_ocr_supported():
    from app.services.document_ocr import is_ocr_supported
    from app.services.ocr_backend import _IMAGE_EXT
    assert is_ocr_supported("photo.HEIC") and is_ocr_supported("scan.jpg")
    assert "heic" in _IMAGE_EXT and "heif" in _IMAGE_EXT


def test_extract_live_ocr_fallback_for_image(monkeypatch):
    monkeypatch.setattr(dop, "_live_ocr", lambda conn, did: "Recipient jl@x.com per statement")
    with engine.connect() as conn:
        text, method = dop.extract_document_text(conn, {"id": -1, "original_name": "scan.jpg"}, None, ocr=True)
    assert method == "ocr" and "jl@x.com" in text


def test_extract_no_ocr_when_disabled():
    with engine.connect() as conn:
        text, method = dop.extract_document_text(conn, {"id": -1, "original_name": "scan.jpg"}, None, ocr=False)
    assert method == "image_no_text" and text == ""


def test_live_ocr_guarded_when_backend_unavailable():
    # dev/CI has no tesseract engine -> build_production_extractor raises -> _live_ocr returns "" safely
    with engine.connect() as conn:
        assert dop._live_ocr(conn, -1) == ""


def test_scanned_pdf_uses_ocr_when_native_text_insufficient(monkeypatch, tmp_path):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4 fake")                        # no real text layer
    monkeypatch.setattr(dop, "_pdf_text", lambda p: "")    # native yields nothing
    monkeypatch.setattr(dop, "_live_ocr", lambda conn, did: "Dear jl@x.com, your 1095-A")
    with engine.connect() as conn:
        text, method = dop.extract_document_text(conn, {"id": -1, "original_name": "scan.pdf"}, f, ocr=True)
    assert method == "ocr" and "jl@x.com" in text


# --- UI --------------------------------------------------------------------------------------

def test_review_template_shows_proposal_confidence_and_evidence():
    from app.routes.admin import templates
    from app.security.models import Principal
    p = Principal(1, "a@e.com", "Admin", frozenset({"client.write"}))
    doc = {"id": 457, "name": "a.pdf", "doc_type": None, "year": None, "current_owner": "Unassigned (NULL)",
           "view_url": "/documents/457/download?inline=1", "download_url": "/documents/457/download",
           "proposal": {"proposed_entity_type": "person", "proposed_entity_id": 7430,
                        "proposed_entity_name": "MARY HARDY", "confidence": "HIGH",
                        "evidence": ["✓ exact name 'MARY HARDY'", "✓ phone ending 9034 matched"],
                        "competing": [], "best_candidates": [], "extraction_method": "pdf_text"}}
    folder_cand = [{"id": 5284, "name": "Adrianna Lomasney", "designation": "Client", "email": None,
                    "phone": None, "household_name": None, "household_id": None, "link": "#"}]
    html = templates.get_template("admin/unassigned_review.html").render(
        request=None, principal=p, folder="Adrianna Hardy", eligible_docs=[doc],
        already_owned_docs=[], excluded_docs=[], candidates=folder_cand,
        household_candidates=[], org_candidates=[])
    # content-analysis box shows proposed owner + confidence + evidence, but NO ownership action button
    assert "PROPOSED OWNER FROM DOCUMENT CONTENT" in html
    assert "MARY HARDY" in html and "HIGH confidence" in html
    assert "✓ phone ending 9034 matched" in html
    assert "Preview → proposed owner" not in html and "Preview → " not in html  # no Preview ownership button
    # the recommended candidate appears as an "Assign to …" button marked Recommended
    assert "Assign to MARY HARDY (#7430)" in html
    assert "✓ Recommended · HIGH confidence" in html
    # the alternative folder candidate is also an "Assign to …" button (not duplicated with the recommendation)
    assert "Assign to Adrianna Lomasney (#5284)" in html
    assert "SOURCE FOLDER / FILENAME suggestions" in html
