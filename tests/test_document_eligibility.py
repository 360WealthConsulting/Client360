"""Only real documents may enter document intelligence.

An entire Drake application tree was copied into SharePoint and ingested by importers that apply no
type filter, so program binaries, fonts, help files and per-client data blobs became owner-proposal
candidates. Eligibility is decided by a POSITIVE document-family map and applied at the ANALYSIS
boundary — ingestion, provenance and auditability are deliberately untouched.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.db import documents, engine
from app.services.document_eligibility import (
    DOC_FAMILY,
    ELIGIBLE_EXTENSIONS,
    document_family,
    is_intelligence_eligible,
)
from app.services.document_high_validation import _unassigned_ids

_RECOGNIZED = ["statement.pdf", "letter.docx", "letter.doc", "notes.rtf", "book.xlsx", "book.xls",
               "data.csv", "deck.pptx", "scan.tiff", "scan.tif", "photo.jpg", "photo.jpeg",
               "photo.png", "photo.heic", "photo.heif", "readme.txt", "mail.msg", "mail.eml",
               "export.xml", "invite.ics"]

# Representative Drake/program/runtime artifacts observed in the production UNSUPPORTED bucket.
_RUNTIME = ["DrakeUI.dll", "CLIENT.000", "setup.exe", "arial.ttf", "help.hlp", "data.di6",
            "data.ei6", "record.ef", "state.per", "keys.crp", "index.ptr", "map.fid",
            "cache.pi9", "table.sbs", "list.ld", "chart.pg", "cfg.itc2", "legacy.dbf",
            "blob.dat", "archive.download"]


@pytest.mark.parametrize("name", _RECOGNIZED)
def test_recognized_document_families_are_eligible(name):
    assert is_intelligence_eligible(name) is True
    assert document_family(name) != "other"


@pytest.mark.parametrize("name", _RUNTIME)
def test_program_and_runtime_artifacts_are_not_eligible(name):
    assert is_intelligence_eligible(name) is False
    assert document_family(name) == "other"


def test_every_ocr_supported_extension_is_eligible():
    """A file the OCR engine can read must never be excluded from analysis."""
    from app.services.document_ocr import SUPPORTED_EXT
    assert SUPPORTED_EXT <= ELIGIBLE_EXTENSIONS, SUPPORTED_EXT - ELIGIBLE_EXTENSIONS


def test_content_type_rescues_a_document_with_no_extension():
    assert is_intelligence_eligible("scan", "application/pdf") is True
    assert is_intelligence_eligible("scan") is False


def test_octet_stream_grants_nothing():
    """The catch-all content type is exactly what the Drake blobs carry."""
    assert is_intelligence_eligible("CLIENT.000", "application/octet-stream") is False
    assert is_intelligence_eligible("noext", "application/octet-stream") is False


def test_a_content_type_can_never_override_an_ineligible_extension():
    assert is_intelligence_eligible("DrakeUI.dll", "application/pdf") is False


def test_the_sharepoint_importer_uses_this_one_map():
    """Labelling and eligibility must not drift into two maps."""
    from app.importers import sharepoint
    assert sharepoint._DOC_FAMILY is DOC_FAMILY
    assert sharepoint.sharepoint_doc_type("statement.pdf") == "pdf"
    assert sharepoint.sharepoint_doc_type("mail.msg") == "email_attachment"
    assert sharepoint.sharepoint_doc_type("DrakeUI.dll") == "other"


# --- the analysis boundary ---------------------------------------------------------------------

def _document(name):
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        return c.execute(documents.insert().values(
            original_name=name, stored_name=f"elig:{tag}", storage_path=f"/t/{tag}",
            size_bytes=1, sha256=tag * 4, status="active", archived=False,
        ).returning(documents.c.id)).scalar_one()


def test_an_ineligible_document_is_excluded_from_ownership_analysis():
    runtime_id = _document("DrakeUI.dll")
    doc_id = _document("statement.pdf")
    with engine.connect() as c:
        candidates = _unassigned_ids(c)
    assert doc_id in candidates, "a real document must still be analysed"
    assert runtime_id not in candidates, "a program binary must not be an ownership candidate"


def test_an_ineligible_document_remains_present_and_queryable():
    """Eligibility decides ANALYSIS participation only — it never hides or deletes a row."""
    runtime_id = _document("CLIENT.000")
    with engine.connect() as c:
        row = c.execute(text(
            "SELECT id, original_name, status, archived FROM documents WHERE id = :d"),
            {"d": runtime_id}).mappings().one_or_none()
    assert row is not None, "the historical row must remain fully auditable"
    assert row["original_name"] == "CLIENT.000"
    assert row["status"] == "active" and row["archived"] is False


def test_provenance_for_an_ineligible_document_is_untouched():
    runtime_id = _document("setup.exe")
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_sources (document_id, source_system, source_uri) "
                       "VALUES (:d, 'SharePoint', :u)"), {"d": runtime_id, "u": f"sp://{runtime_id}"})
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM document_sources WHERE document_id = :d"),
                         {"d": runtime_id}).scalar() == 1


@pytest.mark.parametrize("name", ["statement.pdf", "scan.tiff", "letter.docx", "book.xlsx"])
def test_eligible_document_records_remain_candidates(name):
    doc_id = _document(name)
    with engine.connect() as c:
        assert doc_id in _unassigned_ids(c)
