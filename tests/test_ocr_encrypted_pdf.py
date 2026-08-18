"""Phase 1D — encrypted / password-protected PDFs are a DISTINCT terminal outcome.

An encrypted PDF must NOT be treated as an ordinary (retryable) OCR failure. It is detected read-only
BEFORE any poppler render / OCR, raised as :class:`OcrEncryptedPdf`, recorded as the terminal
``unsupported`` status with the structured ``password_required:`` last_error (no schema change / no
migration), counted distinctly (``encrypted``), never retried, and the batch continues. Ordinary corrupt
(unencrypted) PDFs still follow the normal failure path; unencrypted PDFs are unchanged. We never guess,
crack, or bypass a password.

Persistence note: a first-class ``encrypted`` DB status could be introduced in a later schema-evolution
phase; it is NOT required for the correct no-retry / operator-visible behavior proven here.
"""
from __future__ import annotations

import pathlib
import tempfile
import uuid

import pytest
from sqlalchemy import delete, func, insert, select

from app.db import document_ocr, documents, engine
from app.services import document_ocr as doc_ocr
from app.services.ocr_backend import OcrDeps, build_extractor
from app.services.ocr_exceptions import ENCRYPTED_PDF_LAST_ERROR, OcrEncryptedPdf

_DBL = "tests.ocr_doubles"


@pytest.fixture(autouse=True)
def _cleanup_created_docs():
    with engine.connect() as c:
        high = c.execute(select(func.max(documents.c.id))).scalar() or 0
    yield
    with engine.begin() as c:
        ids = [r[0] for r in c.execute(select(documents.c.id).where(documents.c.id > high))]
        if ids:
            c.execute(delete(document_ocr).where(document_ocr.c.document_id.in_(ids)))
            c.execute(delete(documents).where(documents.c.id.in_(ids)))


def _doc(name="scan.pdf"):
    sfx = uuid.uuid4().hex
    with engine.begin() as c:
        return c.execute(insert(documents).values(
            original_name=name, stored_name=f"s-{sfx}", storage_provider="Client360 Local",
            storage_path=f"/nonexistent/{sfx}", size_bytes=1, sha256=(sfx + sfx)[:64],
            status="active", archived=False).returning(documents.c.id)).scalar_one()


def _ocr_state(doc_id):
    with engine.connect() as c:
        return c.execute(select(document_ocr.c.status, document_ocr.c.attempts,
                                document_ocr.c.last_error).where(
            document_ocr.c.document_id == doc_id)).mappings().one_or_none()


def _doc_ocr_status_mirror(doc_id):
    with engine.connect() as c:
        return c.execute(select(documents.c.ocr_status).where(documents.c.id == doc_id)).scalar_one()


def _retry_candidate_ids():
    with engine.connect() as c:
        return {r[0] for r in c.execute(
            select(documents.c.id).select_from(
                documents.outerjoin(document_ocr, document_ocr.c.document_id == documents.c.id))
            .where(documents.c.status != "deleted",
                   document_ocr.c.status.in_(("failed", "timed_out")),
                   document_ocr.c.attempts < 3))}


# --- 1. detection happens BEFORE any render/OCR ------------------------------

def _deps(*, encrypted, texts=("real selectable text here plenty",)):
    calls = {"text": 0, "render": 0, "ocr": 0}

    def pdf_page_texts(p):
        calls["text"] += 1
        return list(texts)

    def render_pdf_page(p, i):
        calls["render"] += 1
        return "img"

    def ocr_image(img):
        calls["ocr"] += 1
        return "ocr text"

    deps = OcrDeps(pdf_page_texts, render_pdf_page, lambda p: [], ocr_image, lambda: "5.0",
                   pdf_is_encrypted=(lambda p: encrypted))
    return deps, calls


def test_encrypted_pdf_detected_before_render_or_ocr():
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "enc.pdf"
        f.write_bytes(b"%PDF-1.4 encrypted")
        deps, calls = _deps(encrypted=True)
        with pytest.raises(OcrEncryptedPdf):
            build_extractor(deps)({"original_name": "enc.pdf", "id": 1}, f)
        assert calls == {"text": 0, "render": 0, "ocr": 0}   # stopped before ALL expensive work


def test_unencrypted_pdf_uses_normal_text_layer_path():
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "ok.pdf"
        f.write_bytes(b"%PDF-1.4")
        deps, _ = _deps(encrypted=False)
        r = build_extractor(deps)({"original_name": "ok.pdf", "id": 2}, f)
        assert r["engine"] == "pdf-text-layer" and "selectable text" in r["text"]


def test_deps_without_encryption_primitive_are_unchanged():
    # Older/fake deps that don't provide pdf_is_encrypted must behave exactly as before (no detection).
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "ok.pdf"
        f.write_bytes(b"%PDF-1.4")
        deps = OcrDeps(lambda p: ["real selectable text here plenty"], lambda p, i: "img",
                       lambda p: [], lambda img: "x", lambda: "5.0")     # no pdf_is_encrypted
        assert build_extractor(deps)({"original_name": "ok.pdf", "id": 3}, f)["engine"] == "pdf-text-layer"


# --- 2. persistence: terminal unsupported + structured reason, distinct count -

def test_encrypted_outcome_persisted_as_unsupported_password_required_in_process():
    # In-process path (isolate=False): exercises the _ocr_one branch directly.
    did = _doc("statement-locked.pdf")

    def extractor(row, path):
        raise OcrEncryptedPdf("encrypted/password-protected PDF: statement-locked.pdf")

    summary = doc_ocr.run_ocr(mode="reprocess", document_ids=[did], isolate=False, extractor=extractor)
    assert summary["encrypted"] == 1
    assert summary["failed"] == 0 and summary["timed_out"] == 0        # NOT a generic failure/timeout
    row = _ocr_state(did)
    assert row["status"] == "unsupported"                              # terminal, allowed status (no migration)
    assert row["last_error"] == ENCRYPTED_PDF_LAST_ERROR              # structured, machine-detectable
    assert row["last_error"].startswith("password_required:")
    assert row["attempts"] == 0                                        # did NOT consume a retry attempt
    assert _doc_ocr_status_mirror(did) == "unsupported"               # mirror consistent with document_ocr


def test_encrypted_outcome_via_subprocess_isolation():
    # Isolated path: the distinct type must survive the child->parent boundary (not degrade to a generic
    # RuntimeError/failed). Preserves Phase 1C fail-closed isolation (explicit factory_ref).
    did = _doc("locked-iso.pdf")
    summary = doc_ocr.run_ocr(mode="reprocess", document_ids=[did], isolate=True,
                              factory_ref=f"{_DBL}.encrypted_pdf_factory", hard_timeout=10, stall_timeout=10)
    assert summary["encrypted"] == 1 and summary["failed"] == 0
    row = _ocr_state(did)
    assert row["status"] == "unsupported" and row["last_error"].startswith("password_required:")


# --- 3. no generic retry loop for encrypted docs -----------------------------

def test_encrypted_doc_is_not_a_retry_candidate():
    did = _doc("nopw.pdf")
    doc_ocr.run_ocr(mode="reprocess", document_ids=[did], isolate=False,
                    extractor=lambda r, p: (_ for _ in ()).throw(OcrEncryptedPdf("locked")))
    assert _ocr_state(did)["status"] == "unsupported" and _ocr_state(did)["attempts"] == 0
    # The retry sweep selects only failed/timed_out rows under max_attempts (_retry_candidate_ids mirrors
    # that exact WHERE clause) — an encrypted doc (terminal 'unsupported', attempts=0) must be excluded, so
    # it never consumes a generic retry attempt.
    assert did not in _retry_candidate_ids()


# --- 4. batch continues past an encrypted document ---------------------------

def test_batch_continues_after_encrypted_document():
    enc = _doc("firstHANGlocked.pdf")   # name carries HANG only for readability; the factory keys on it
    ok = _doc("secondOK.pdf")

    def extractor(row, path):
        if "LOCKED" in (row.get("original_name") or "").upper():
            raise OcrEncryptedPdf("locked")
        return {"text": f"ok:{row.get('original_name')}", "engine": "fake"}

    summary = doc_ocr.run_ocr(mode="reprocess", document_ids=[enc, ok], isolate=False, extractor=extractor)
    assert summary["encrypted"] == 1 and summary["completed"] == 1
    assert _ocr_state(enc)["status"] == "unsupported"
    assert _ocr_state(ok)["status"] == "completed"                    # the next document processed


# --- 5. ordinary corrupt (unencrypted) PDFs still fail the normal way --------

def test_corrupt_unencrypted_pdf_follows_ordinary_failure_path():
    did = _doc("corrupt.pdf")
    doc_ocr.run_ocr(mode="reprocess", document_ids=[did], isolate=False,
                    extractor=lambda r, p: (_ for _ in ()).throw(RuntimeError("cannot parse PDF")))
    row = _ocr_state(did)
    assert row["status"] == "failed"                                  # retryable generic failure, unchanged
    assert row["attempts"] >= 1                                       # DID consume an attempt (unlike encrypted)
    assert did in _retry_candidate_ids()                              # eligible for retry (unlike encrypted)


# --- 6. distinct telemetry: operators can tell encrypted from unsupported ----

def test_encrypted_is_distinct_from_unsupported_in_summary_and_progress():
    enc = _doc("locked.pdf")
    other = _doc("notes.txt")           # genuinely unsupported file type (not OCR-capable)
    lines = []

    def extractor(row, path):
        raise OcrEncryptedPdf("locked")

    summary = doc_ocr.run_ocr(mode="reprocess", document_ids=[enc, other], isolate=False,
                              extractor=extractor, progress=lines.append)
    assert summary["encrypted"] == 1 and summary["unsupported"] == 1  # counted in SEPARATE buckets
    assert _ocr_state(enc)["last_error"].startswith("password_required:")
    # The .txt is unsupported with NO password_required reason — operators can distinguish the two.
    assert (_ocr_state(other)["last_error"] or "") == ""
    # Progress telemetry surfaces the distinct encrypted bucket.
    assert any("encrypted=1" in ln for ln in lines)


def test_run_tracker_counts_encrypted_distinctly():
    from app.jobs.ocr_status import OcrRunTracker
    with tempfile.TemporaryDirectory() as d:
        tracker = OcrRunTracker(str(pathlib.Path(d) / "status.json"), run_id="t", mode="reprocess", total=2)
        tracker.start()
        tracker.on_document_result(1, "locked.pdf", "encrypted")
        tracker.on_document_result(2, "notes.txt", "unsupported")
        counts = tracker.snapshot()["counts"]
        assert counts["encrypted"] == 1 and counts["unsupported"] == 1
