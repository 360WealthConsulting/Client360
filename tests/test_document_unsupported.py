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
    # A RECOGNIZED document type that still yields no usable text. ".zip" would no longer appear:
    # document-intelligence eligibility keeps non-document artifacts out of the analysis corpus
    # entirely, which is asserted separately below.
    f = tmp_path / "x.docx"
    f.write_bytes(b"not a real docx")
    did = _doc(f, "statement.docx")
    res = du.inventory()
    row = next((r for r in res["rows"] if r["document_id"] == did), None)
    assert row and row["extension"] == "docx" and row["failure_reason"] in ("unsupported", "no_usable_text")
    assert res["by_extension"].get("docx", 0) >= 1
    assert _owner(did) == (None, None, None)               # inventory writes nothing


def test_inventory_ignores_non_document_artifacts(tmp_path):
    """Program/runtime files are not analysis candidates, so they never reach the inventory — while
    the rows themselves remain present and queryable."""
    f = tmp_path / "x.zip"
    f.write_bytes(b"not extractable")
    did = _doc(f, "archive.zip")
    res = du.inventory()
    assert all(r["document_id"] != did for r in res["rows"])
    with engine.connect() as c:
        assert c.execute(select(documents.c.id).where(documents.c.id == did)).scalar() == did


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


# --- EML extractor (Phase 5 step 2) ---------------------------------------------------------------

def _eml(path, *, frm, to, subject, body, attach=None, html=False):
    lines = [f"From: {frm}", f"To: {to}", f"Subject: {subject}",
             "Date: Mon, 01 Jan 2024 10:00:00 -0500", "MIME-Version: 1.0"]
    if html:
        lines += ["Content-Type: text/html; charset=utf-8", "", f"<html><body><p>{body}</p></body></html>"]
    else:
        lines += ["Content-Type: text/plain; charset=utf-8", "", body]
    path.write_text("\r\n".join(lines) + "\r\n")


def test_eml_extraction_headers_and_body(tmp_path):
    f = tmp_path / "m.eml"
    _eml(f, frm=f"Mary <mary-{_TAG}@x.com>", to="advisor@firm.com",
         subject="Tax documents", body="Please find my W-2 attached. Thanks, Mary Hardy")
    text, method = _extract(-1, f, "m.eml")
    assert method == "eml"
    assert f"mary-{_TAG}@x.com" in text and "Tax documents" in text and "Mary Hardy" in text


def test_eml_html_body_converted(tmp_path):
    f = tmp_path / "h.eml"
    _eml(f, frm=f"c-{_TAG}@x.com", to="a@b.com", subject="Hi", body="Hello <b>Mary Hardy</b> world", html=True)
    text, method = _extract(-1, f, "h.eml")
    assert method == "eml" and "Mary Hardy" in text and "<b>" not in text


def test_eml_corrupt_fails_safe(tmp_path):
    f = tmp_path / "bad.eml"
    f.write_bytes(b"\x00\x01\x02 not a message")
    text, method = _extract(-1, f, "bad.eml")
    assert method in ("eml", "unsupported")                # never raises; empty parse -> unsupported
    assert _extract(-1, tmp_path / "missing.eml", "missing.eml")  # missing path handled by caller


def test_eml_feeds_pipeline_matches_via_from(tmp_path):
    email = f"emlp-{_TAG}@mail.com"
    pid = _person(f"Emlina {_A}", email=email)
    f = tmp_path / "p.eml"
    _eml(f, frm=f"Emlina {_A} <{email}>", to="advisor@firm.com", subject="Docs", body="my statement")
    did = _doc(f, "p.eml")
    r = dop.propose_document_owner(did)
    assert r["extraction_method"] == "eml" and r["proposed_entity_id"] == pid


# --- XLS extractor (guarded on xlrd) --------------------------------------------------------------

def test_xls_without_xlrd_stays_unsupported(tmp_path):
    import importlib.util
    if importlib.util.find_spec("xlrd") is not None:
        pytest.skip("xlrd installed; fail-safe path not exercised")
    f = tmp_path / "legacy.xls"
    f.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 fake ole2")     # OLE2 magic but no xlrd -> ""
    text, method = _extract(-1, f, "legacy.xls")
    assert text == "" and method == "unsupported"          # guarded: no dependency -> fail safe


# --- inspection (magic identification, read-only) -------------------------------------------------

def test_inspect_identifies_formats(tmp_path):
    ole = tmp_path / "a.xls"; ole.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest")
    unk = tmp_path / "a.napiers"; unk.write_bytes(b"\x7f\x7f\x7f binaryjunk")
    d_ole = _doc(ole, "a.xls")
    d_unk = _doc(unk, "a.napiers")
    rows = {r["document_id"]: r for r in du.inspect_files([d_ole, d_unk])}
    assert rows[d_ole]["identified"].startswith("OLE2")
    assert rows[d_unk]["identified"] == "unknown / binary"
    assert _owner(d_ole) == (None, None, None)             # inspection writes nothing


def test_inspect_sqlite_lists_tables(tmp_path):
    import sqlite3
    dbp = tmp_path / "store.db"
    con = sqlite3.connect(str(dbp))
    con.execute(f"create table clients_{_A} (id integer, name text)")
    con.commit(); con.close()
    did = _doc(dbp, "store.db")
    row = du.inspect_files([did])[0]
    assert row["identified"].startswith("SQLite")
    assert f"clients_{_A}" in (row.get("sqlite_tables") or [])
