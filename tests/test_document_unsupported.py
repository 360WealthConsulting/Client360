"""Phase 5 — DOCX/ICS extractors + UNSUPPORTED inventory & targeted re-analysis (read-only, no mutation)."""
import hashlib
import uuid
import zipfile

import pytest
from sqlalchemy import select

from app.db import documents, engine, people, person_source_links, source_contacts
from app.services import document_owner_proposal as dop
from app.services import document_unsupported as du

_TAG = uuid.uuid4().hex[:8]
_A = _TAG.translate(str.maketrans("0123456789", "abcdefghij"))
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
    for lst in (_DOCS, _PEOPLE, _SC, _LINKS):
        lst.clear()


def _make_docx(path, text):
    body = ("<?xml version='1.0'?><w:document "
            "xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body>"
            f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>")
    with zipfile.ZipFile(str(path), "w") as z:
        z.writestr("word/document.xml", body)


def _person(full_name, email=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, active=True)
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
        _SC.append(sid); _LINKS.append(lid)
    return pid


def _doc(path, name):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=None, household_id=None, organization_id=None, original_name=name,
            stored_name=f"un-{_TAG}-{uuid.uuid4().hex}", storage_path=str(path), storage_uri=str(path),
            size_bytes=10, sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active",
            archived=False, tags={"source_system": "TaxDome Drive"}
        ).returning(documents.c.id)).scalar_one()
    _DOCS.append(did)
    return did


def _extract(did, path, name):
    with engine.connect() as c:
        return dop.extract_document_text(c, {"id": did, "original_name": name}, path, ocr=False)


def _owner(did):
    with engine.connect() as c:
        return tuple(c.execute(select(documents.c.person_id, documents.c.household_id,
                                      documents.c.organization_id).where(documents.c.id == did)).first())


# --- DOCX extractor --------------------------------------------------------------------------------

def test_docx_extraction_success(tmp_path):
    f = tmp_path / "letter.docx"
    _make_docx(f, f"Dear Mary Hardy, contact m-{_TAG}@x.com")
    text, method = _extract(-1, f, "letter.docx")
    assert method == "docx" and "Mary Hardy" in text and f"m-{_TAG}@x.com" in text


def test_docx_corrupt_fails_safe(tmp_path):
    f = tmp_path / "bad.docx"
    f.write_bytes(b"not a zip file")
    text, method = _extract(-1, f, "bad.docx")
    assert text == "" and method == "unsupported"          # corrupt -> safe, stays unsupported


def test_docx_feeds_pipeline_and_matches(tmp_path):
    email = f"docxp-{_TAG}@mail.com"
    pid = _person(f"Docxina {_A}", email=email)
    f = tmp_path / "d.docx"
    _make_docx(f, f"Statement for Docxina {_A}, remit to {email}")
    did = _doc(f, "d.docx")
    r = dop.propose_document_owner(did)
    assert r["extraction_method"] == "docx"
    assert (r["proposed_entity_id"], r["confidence"]) == (pid, "HIGH")
    assert _owner(did) == (None, None, None)               # no ownership mutation


# --- ICS extractor ---------------------------------------------------------------------------------

def test_ics_extraction_success(tmp_path):
    f = tmp_path / "invite.ics"
    f.write_text("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
                 "SUMMARY:Tax review meeting\r\n"
                 f"ORGANIZER;CN=Mary Hardy:mailto:org-{_TAG}@x.com\r\n"
                 f"ATTENDEE;CN=John Smith:mailto:att-{_TAG}@x.com\r\n"
                 "END:VEVENT\r\nEND:VCALENDAR\r\n")
    text, method = _extract(-1, f, "invite.ics")
    assert method == "ics"
    assert "Tax review meeting" in text and "Mary Hardy" in text and f"org-{_TAG}@x.com" in text


def test_ics_empty_stays_unsupported(tmp_path):
    f = tmp_path / "empty.ics"
    f.write_text("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
    text, method = _extract(-1, f, "empty.ics")
    assert text == "" and method == "unsupported"


def test_ics_feeds_pipeline_matches_via_email(tmp_path):
    email = f"icsp-{_TAG}@mail.com"
    pid = _person(f"Icsina {_A}", email=email)
    f = tmp_path / "m.ics"
    f.write_text("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Review\r\n"
                 f"ATTENDEE;CN=Icsina {_A}:mailto:{email}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    did = _doc(f, "m.ics")
    r = dop.propose_document_owner(did)
    assert r["extraction_method"] == "ics" and r["proposed_entity_id"] == pid


# --- genuinely unsupported stays unsupported -------------------------------------------------------

def test_unknown_extension_stays_unsupported(tmp_path):
    f = tmp_path / "x.zip"
    f.write_bytes(b"PK\x03\x04 whatever")
    text, method = _extract(-1, f, "x.zip")
    assert text == "" and method == "unsupported"


# --- inventory + reanalyze (read-only / no mutation) ----------------------------------------------

def test_inventory_lists_unsupported_and_no_mutation(tmp_path):
    f = tmp_path / "x.zip"
    f.write_bytes(b"not extractable")
    did = _doc(f, "archive.zip")                           # unknown type -> UNSUPPORTED
    res = du.inventory()
    row = next((r for r in res["rows"] if r["document_id"] == did), None)
    assert row and row["extension"] == "zip" and row["failure_reason"] in ("unsupported", "no_usable_text")
    assert res["by_extension"].get("zip", 0) >= 1
    assert _owner(did) == (None, None, None)               # inventory writes nothing


def test_reanalyze_recovers_docx_from_unsupported(tmp_path):
    # a .docx with no cache would be UNSUPPORTED only if extractor missing — here the docx extractor
    # recovers it, so reanalyze should NOT leave it UNSUPPORTED and should count it as newly-text.
    email = f"re-{_TAG}@mail.com"
    _person(f"Recovina {_A}", email=email)
    f = tmp_path / "r.docx"
    _make_docx(f, f"Statement for Recovina {_A}, remit to {email}")
    did = _doc(f, "r.docx")
    res = du.reanalyze(doc_ids=[did])
    assert res["after_counts"].get("UNSUPPORTED", 0) == 0
    assert res["newly_text"] == 1 and res["newly_identity"] == 1
    assert _owner(did) == (None, None, None)               # no ownership assigned


def test_reanalyze_reports_remaining_for_true_unsupported(tmp_path):
    f = tmp_path / "x.zip"
    f.write_bytes(b"nope")
    did = _doc(f, "still.zip")
    res = du.reanalyze(doc_ids=[did])
    assert res["after_counts"].get("UNSUPPORTED", 0) == 1
    assert did in {r["document_id"] for r in res["remaining"]}
