"""Tax-year inference and its READ-ONLY preview.

``documents`` has no ``tax_year`` column, so the Documents tab's year column and year filter have
always been empty for SharePoint-backed documents. This derives a year for DISPLAY from evidence the
firm already files by — the filename and SharePoint's own year folders — and reports the evidence
behind each proposal so a reviewer can judge it before any backfill is authorised.

Nothing here writes. ``preview_tax_years`` is the whole mechanism: it reports what a backfill WOULD
say, and every row it returns carries ``would_write: False``.
"""
import pytest

from app.services.document_tax_year import (
    infer_tax_year,
    preview_summary,
    preview_tax_years,
    source_path_for,
)

_SITE = "https://360financialsolutions.sharepoint.com/sites/360Data/Shared%20Documents"
_CLIENT = "360%20Tax%20Solutions,%20LLC/Clients/Tax%20Preparation/Individual/WHITE,%20MICHAEL%20AND%20DEBRA"


def _row(name, *, folder="", tags=None, doc_id=1):
    url = f"{_SITE}/{_CLIENT}{('/' + folder) if folder else ''}/{name.replace(' ', '%20')}"
    return {"id": doc_id, "original_name": name, "storage_path": f"/x/{name}",
            "tags": {"source_system": "SharePoint", "web_url": url, **(tags or {})}}


# --- filename evidence -------------------------------------------------------------------------

def test_single_year_in_filename_is_strong():
    p = infer_tax_year(_row("Debs 2022 W2.pdf"))
    assert p.year == 2022 and p.confidence == "strong" and p.source == "filename"
    assert p.is_proposed


def test_several_years_in_the_filename_is_a_conflict_not_a_guess():
    p = infer_tax_year(_row("2021 and 2022 comparison.pdf"))
    assert p.year is None and p.confidence == "conflict" and not p.is_proposed


def test_hash_fragments_and_scanner_names_are_not_years():
    """Inherited from document_naming.extract_years — not re-implemented with a looser regex."""
    assert infer_tax_year(_row("c4aa9e2000fragment.pdf")).year is None
    assert infer_tax_year(_row("16479761267705803283594607293797.jpg")).year is None


# --- folder evidence ---------------------------------------------------------------------------

def test_year_folder_alone_is_moderate():
    p = infer_tax_year(_row("Supporting Docs.pdf", folder="2020"))
    assert p.year == 2020 and p.confidence == "moderate" and p.source == "folder"
    assert p.is_proposed


def test_year_folder_with_a_label_still_counts():
    assert infer_tax_year(_row("Receipt.pdf", folder="2023%20Receipts")).year == 2023


def test_filename_and_folder_agreeing_is_strong():
    p = infer_tax_year(_row("2021 Tax Return Documents.pdf", folder="2021"))
    assert p.year == 2021 and p.confidence == "strong" and p.source == "filename+folder"
    assert "filename and folder agree" in p.evidence


def test_filename_and_folder_disagreeing_proposes_nothing():
    p = infer_tax_year(_row("2019 carryforward.pdf", folder="2021"))
    assert p.year is None and p.confidence == "conflict"
    assert "filename and folder disagree" in p.evidence


def test_a_year_inside_the_client_folder_name_is_not_the_document_year():
    """``CLAY, BENJAMIN & JENETTA 2018`` is a client folder, not a filing year."""
    row = {"id": 1, "original_name": "Signed 8879.pdf", "storage_path": "",
           "tags": {"web_url": f"{_SITE}/Clients/CLAY,%20BENJAMIN%20&%20JENETTA%202018/Signed%208879.pdf"}}
    assert infer_tax_year(row).year is None


def test_no_year_anywhere():
    p = infer_tax_year(_row("Voided Check.pdf"))
    assert p.year is None and p.confidence == "none" and p.evidence == []


# --- a recorded year always wins ----------------------------------------------------------------

def test_a_recorded_tax_year_is_authoritative():
    p = infer_tax_year(_row("something.pdf", folder="2020", tags={"tax_year": "2017"}))
    assert p.year == 2017 and p.source == "recorded"


# --- path extraction ---------------------------------------------------------------------------

def test_source_path_is_decoded_from_the_sharepoint_url():
    path = source_path_for(_row("Debs 2022 W2.pdf", folder="2022"))
    assert path.endswith("WHITE, MICHAEL AND DEBRA/2022/Debs 2022 W2.pdf")
    assert "%20" not in path


def test_source_path_falls_back_to_the_stored_path():
    assert source_path_for({"original_name": "a.pdf", "storage_path": "/vault/a.pdf",
                            "tags": {}}) == "/vault/a.pdf"


# --- the preview mechanism ----------------------------------------------------------------------

def test_preview_reports_evidence_and_never_writes():
    rows = [_row("Debs 2022 W2.pdf", doc_id=1),
            _row("Supporting Docs.pdf", folder="2020", doc_id=2),
            _row("Voided Check.pdf", doc_id=3),
            _row("2021 and 2022 comparison.pdf", doc_id=4)]
    preview = preview_tax_years(rows)
    assert [p["proposed_year"] for p in preview] == [2022, 2020, None, None]
    assert all(p["would_write"] is False for p in preview)
    assert all("evidence" in p and "confidence" in p for p in preview)
    assert preview[0]["evidence"], "a proposal must say why"
    assert preview[3]["confidence"] == "conflict"


def test_preview_summary_counts_by_confidence():
    rows = [_row("Debs 2022 W2.pdf", doc_id=1),
            _row("Supporting Docs.pdf", folder="2020", doc_id=2),
            _row("Voided Check.pdf", doc_id=3)]
    summary = preview_summary(rows)
    assert summary["total"] == 3 and summary["proposed"] == 2
    assert summary["years"] == [2020, 2022]
    assert summary["by_confidence"] == {"strong": 1, "moderate": 1, "none": 1}


@pytest.mark.parametrize("bad", [None, {}, {"tags": None}, {"original_name": None}])
def test_inference_never_raises_on_incomplete_rows(bad):
    assert infer_tax_year(bad or {}).year is None
