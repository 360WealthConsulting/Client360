"""Production OCR backend (PR 5B) — extraction coverage.

The concrete engine behind the PR 5A ``extractor`` interface: PDF text-layer extraction with a Tesseract
fallback only for image-only pages, plus PNG/JPG/TIFF (incl. multi-page) OCR, page counts, engine/version
recording, and clear backend-unavailable / extraction-failure errors. Extraction primitives are injected
as fakes so every path is deterministic and CI needs no OCR libraries. Also exercises the backend
end-to-end through ``run_ocr`` (canonical enrichment, search, no duplicate rows). Temp files + test rows.
"""
import uuid

import pytest
from sqlalchemy import delete, select

from app.db import document_ocr, documents, engine, people
from app.security.models import Principal
from app.services import document_ocr as ocr
from app.services.document_ocr import OcrBackendUnavailable
from app.services.ocr_backend import (
    OcrDeps,
    build_extractor,
    build_production_extractor,
    preflight,
)
from app.services.universal_search import universal_search

_TAG = "OCRBACK"
_CAPS = frozenset({"client.read", "documents.view", "record.read_all"})


@pytest.fixture(autouse=True)
def _clean():
    def _wipe():
        with engine.begin() as c:
            doc_ids = list(c.scalars(select(documents.c.id).where(
                documents.c.original_name.like(f"%{_TAG}%"))))
            if doc_ids:
                c.execute(delete(document_ocr).where(document_ocr.c.document_id.in_(doc_ids)))
                c.execute(delete(documents).where(documents.c.id.in_(doc_ids)))
            pids = list(c.scalars(select(people.c.id).where(people.c.full_name.like(f"%{_TAG}%"))))
            if pids:
                c.execute(delete(people).where(people.c.id.in_(pids)))
    _wipe()
    yield
    _wipe()


def _deps(*, pdf_texts=None, ocr_text="ocr text", version="5.3.1", pages=1, ocr_fn=None):
    calls = {"render": 0, "ocr": 0}

    def pdf_page_texts(p):
        return list(pdf_texts or [])

    def render_pdf_page(p, i):
        calls["render"] += 1
        return f"rendered-page-{i}"

    def image_pages(p):
        return [f"frame-{k}" for k in range(pages)]

    def ocr_image(img):
        calls["ocr"] += 1
        return ocr_fn(img) if ocr_fn else ocr_text

    def engine_version():
        return version

    return OcrDeps(pdf_page_texts, render_pdf_page, image_pages, ocr_image, engine_version), calls


def _file(tmp_path, name, content=b"%PDF-1.4 fake"):
    p = tmp_path / name
    p.write_bytes(content)
    return p


def _row(name, path):
    return {"id": 1, "original_name": name, "storage_uri": str(path)}


# --- PDF: text layer vs OCR fallback -----------------------------------------

def test_searchable_pdf_uses_text_layer_no_image_ocr(tmp_path):
    deps, calls = _deps(pdf_texts=["This is a genuine selectable text layer with plenty of content."])
    ex = build_extractor(deps)
    r = ex(_row(f"{_TAG} plan.pdf", _file(tmp_path, "plan.pdf")), _file(tmp_path, "plan.pdf"))
    assert "selectable text layer" in r["text"]
    assert r["engine"] == "pdf-text-layer" and r["page_count"] == 1
    assert calls["render"] == 0 and calls["ocr"] == 0        # no wasted image OCR


def test_scanned_pdf_falls_back_to_tesseract(tmp_path):
    deps, calls = _deps(pdf_texts=[""], ocr_text="scanned invoice total 4200")
    ex = build_extractor(deps)
    f = _file(tmp_path, "scan.pdf")
    r = ex(_row(f"{_TAG} scan.pdf", f), f)
    assert r["text"] == "scanned invoice total 4200"
    assert "tesseract 5.3.1" in r["engine"] and calls["render"] == 1 and calls["ocr"] == 1


def test_multi_page_pdf_mixed(tmp_path):
    deps, calls = _deps(pdf_texts=["real page one has selectable text", "", "third page selectable text"],
                        ocr_text="ocr of page two")
    ex = build_extractor(deps)
    f = _file(tmp_path, "multi.pdf")
    r = ex(_row(f"{_TAG} multi.pdf", f), f)
    assert r["page_count"] == 3 and calls["render"] == 1 and calls["ocr"] == 1
    assert "ocr of page two" in r["text"] and "third page selectable text" in r["text"]


# --- images ------------------------------------------------------------------

@pytest.mark.parametrize("ext", ["png", "jpg", "jpeg", "tif", "tiff"])
def test_image_ocr(tmp_path, ext):
    deps, calls = _deps(ocr_text=f"image text {ext}", pages=1)
    ex = build_extractor(deps)
    f = _file(tmp_path, f"img.{ext}")
    r = ex(_row(f"{_TAG} img.{ext}", f), f)
    assert r["text"] == f"image text {ext}" and r["page_count"] == 1
    assert r["engine"] == "tesseract 5.3.1" and calls["ocr"] == 1


def test_multi_page_tiff(tmp_path):
    deps, calls = _deps(ocr_text="frame text", pages=3)
    ex = build_extractor(deps)
    f = _file(tmp_path, "multi.tiff")
    r = ex(_row(f"{_TAG} multi.tiff", f), f)
    assert r["page_count"] == 3 and calls["ocr"] == 3
    assert r["text"].count("frame text") == 3


# --- error paths -------------------------------------------------------------

def test_extraction_failure_propagates(tmp_path):
    def boom(_img):
        raise RuntimeError("tesseract crashed")
    deps, _ = _deps(ocr_fn=boom)
    ex = build_extractor(deps)
    f = _file(tmp_path, "bad.png")
    with pytest.raises(RuntimeError, match="tesseract crashed"):
        ex(_row(f"{_TAG} bad.png", f), f)


def test_missing_file_raises(tmp_path):
    deps, _ = _deps()
    ex = build_extractor(deps)
    with pytest.raises(FileNotFoundError):
        ex(_row(f"{_TAG} gone.pdf", tmp_path / "nope.pdf"), None)


def test_backend_unavailable_when_libs_missing():
    # CI has no OCR libraries installed, so the production backend must report unavailable cleanly.
    with pytest.raises(OcrBackendUnavailable):
        build_production_extractor()
    r = preflight()
    assert r["ok"] is False and r["errors"]                  # honest health check, not a crash


# --- end-to-end through run_ocr ---------------------------------------------

def _doc(tmp_path, name, *, person_id=None, sha=None, content=b"data"):
    f = _file(tmp_path, name.replace(" ", "_"), content)
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=name, stored_name=f"{name}-{uuid.uuid4().hex[:8]}",
            storage_path=str(f), storage_provider="Client360 Local", storage_uri=str(f),
            size_bytes=len(content), sha256=sha or (uuid.uuid4().hex + uuid.uuid4().hex),
            person_id=person_id, status="active", archived=False).returning(documents.c.id)).scalar_one()
    return did


def test_backend_end_to_end_extracts_and_indexes(tmp_path):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            first_name="Back", last_name="End", full_name=f"Back End {_TAG}",
            active=True).returning(people.c.id)).scalar_one()
    token = f"balancesheet{uuid.uuid4().hex[:6]}"
    did = _doc(tmp_path, f"{_TAG} statement.pdf", person_id=pid)
    deps, _ = _deps(pdf_texts=[f"assets liabilities {token} equity totals here"])
    s = ocr.run_ocr(document_ids=[did], extractor=build_extractor(deps))
    assert s["completed"] == 1
    row = _ocr(did)
    assert row["status"] == "completed" and row["engine"] == "pdf-text-layer"
    assert token in row["text"]
    # Universal Search finds it by extracted text.
    res = universal_search(Principal(0, "a@e.test", "A", _CAPS), token, types=["document"])
    assert any(r["id"] == did for r in res["results"])


def test_backend_no_duplicate_document_or_ocr_rows(tmp_path):
    did = _doc(tmp_path, f"{_TAG} once.pdf", sha="a" * 64)
    deps, _ = _deps(pdf_texts=["selectable content for the document body"])
    with engine.connect() as c:
        n_docs_before = c.execute(select(documents.c.id)).rowcount
    ocr.run_ocr(document_ids=[did], extractor=build_extractor(deps))
    ocr.run_ocr(document_ids=[did], extractor=build_extractor(deps), mode="reprocess")
    with engine.connect() as c:
        n_docs_after = c.execute(select(documents.c.id)).rowcount
        n_ocr = c.execute(select(document_ocr.c.id).where(document_ocr.c.document_id == did)).rowcount
    assert n_docs_after == n_docs_before and n_ocr == 1


def _ocr(did):
    with engine.connect() as c:
        return c.execute(select(document_ocr).where(document_ocr.c.document_id == did)).mappings().first()
