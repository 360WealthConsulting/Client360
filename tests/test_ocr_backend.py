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


def _no_engine(monkeypatch):
    """Force the Tesseract ENGINE probe to fail, leaving the Python wrappers importable.

    This is the real production hazard and the exact CI shape: requirements.txt installs pytesseract /
    pdf2image / Pillow on every host, but they are only shims over the Tesseract and Poppler EXECUTABLES.
    Asserting on the absence of the Python packages (as these tests once did) is not a contract at all —
    it silently stopped testing anything the moment the wrappers were added to requirements.txt. Driving
    the engine probe makes the outcome identical on a bare CI runner and on a developer Mac with
    Tesseract installed."""
    import pytesseract

    def _boom():
        raise OSError("tesseract is not installed or it's not in your PATH")

    monkeypatch.setattr(pytesseract, "get_tesseract_version", _boom)


def test_backend_unavailable_when_engine_missing(monkeypatch):
    # Wrappers importable, engine unreachable -> unavailable cleanly, with the typed error.
    _no_engine(monkeypatch)
    with pytest.raises(OcrBackendUnavailable):
        build_production_extractor()
    r = preflight()
    assert r["ok"] is False and r["errors"]                  # honest health check, not a crash


def test_backend_available_when_engine_is_reachable(monkeypatch):
    # The mirror image: wrappers AND a reachable engine -> a usable backend, no false alarm. Together
    # with the test above this pins BOTH directions, so the check can neither regress to "always
    # available" (the bug) nor over-correct to "never available".
    import pytesseract
    monkeypatch.setattr(pytesseract, "get_tesseract_version", lambda: "5.3.4")
    assert build_production_extractor() is not None
    assert preflight()["engine"] == "tesseract 5.3.4"


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
    s = ocr.run_ocr(isolate=False, document_ids=[did], extractor=build_extractor(deps))
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
    ocr.run_ocr(isolate=False, document_ids=[did], extractor=build_extractor(deps))
    ocr.run_ocr(isolate=False, document_ids=[did], extractor=build_extractor(deps), mode="reprocess")
    with engine.connect() as c:
        n_docs_after = c.execute(select(documents.c.id)).rowcount
        n_ocr = c.execute(select(document_ocr.c.id).where(document_ocr.c.document_id == did)).rowcount
    assert n_docs_after == n_docs_before and n_ocr == 1


def _ocr(did):
    with engine.connect() as c:
        return c.execute(select(document_ocr).where(document_ocr.c.document_id == did)).mappings().first()


# --- OCR resilience: bounded per page + per document, timeout isolated, cache preserved on resume ------

from app.services.document_ocr import OcrTimeout  # noqa: E402


def test_page_hang_raises_ocr_timeout(tmp_path, monkeypatch):
    import time
    monkeypatch.setenv("OCR_PAGE_TIMEOUT_SECONDS", "1")

    def slow(_img):
        time.sleep(10)                                       # a stuck scanned page
        return "never returns in time"
    deps, calls = _deps(pdf_texts=[""], ocr_fn=slow)
    ex = build_extractor(deps)
    f = _file(tmp_path, "scan.pdf")
    with pytest.raises(OcrTimeout):
        ex(_row(f"{_TAG} scan.pdf", f), f)                   # page timeout fires, does not hang


def test_document_deadline_stops_multi_page_scan(tmp_path, monkeypatch):
    import time
    monkeypatch.setenv("OCR_PAGE_TIMEOUT_SECONDS", "10")     # pages individually fine...
    monkeypatch.setenv("OCR_DOCUMENT_TIMEOUT_SECONDS", "1")  # ...but the whole document is capped

    def slow(_img):
        time.sleep(0.6)
        return "page text"
    deps, calls = _deps(pdf_texts=["", "", "", "", ""], ocr_fn=slow)   # 5 image-only pages
    ex = build_extractor(deps)
    f = _file(tmp_path, "big.pdf")
    with pytest.raises(OcrTimeout):
        ex(_row(f"{_TAG} big.pdf", f), f)
    assert calls["ocr"] < 5                                  # stopped before OCR'ing every page


def test_run_ocr_isolates_timeout_and_continues(tmp_path):
    # One bad (hanging) document among many must not stop the rest — it is recorded timed_out and skipped.
    ids = [_doc(tmp_path, f"{_TAG} doc{i}.pdf", sha=(f"{i}" * 64)[:64]) for i in range(5)]
    bad = ids[2]

    def extractor(row, path):
        if row["id"] == bad:
            raise OcrTimeout("page 1/1 timed out")
        return {"text": f"good text for {row['id']}", "engine": "tesseract 5", "page_count": 1}
    s = ocr.run_ocr(isolate=False, document_ids=ids, extractor=extractor, mode="reprocess")
    assert s["timed_out"] == 1 and s["completed"] == 4       # 4 of 5 processed despite the poison doc
    assert _ocr(bad)["status"] == "timed_out"
    assert all(_ocr(d)["status"] == "completed" for d in ids if d != bad)
    assert all(_ocr(d)["text"] for d in ids if d != bad)     # successful docs retain their OCR text


def test_cached_ocr_skipped_and_text_retained_on_resume(tmp_path):
    did = _doc(tmp_path, f"{_TAG} cached.pdf", sha="c" * 64)
    deps, _ = _deps(pdf_texts=["real selectable content for the caching test body"])
    assert ocr.run_ocr(isolate=False, document_ids=[did], extractor=build_extractor(deps))["completed"] == 1
    text1 = _ocr(did)["text"]
    # resume (incremental, NOT reprocess) -> cached, skipped, text retained (no re-OCR)
    s2 = ocr.run_ocr(isolate=False, document_ids=[did], extractor=build_extractor(deps), mode="incremental")
    assert s2["skipped"] == 1 and s2["completed"] == 0
    assert _ocr(did)["text"] == text1


def test_run_ocr_summary_exposes_timed_out_counter(tmp_path):
    did = _doc(tmp_path, f"{_TAG} to.pdf", sha="t" * 64)

    def extractor(row, path):
        raise OcrTimeout("page 1/1 timed out")
    s = ocr.run_ocr(isolate=False, document_ids=[did], extractor=extractor, mode="reprocess")
    assert "timed_out" in s and s["timed_out"] == 1 and s["completed"] == 0
    assert s["status"] == "completed"                        # timeout is recorded, not a batch error


# --- terminal OCR state for documents that never pass through OCR (native text / non-OCR types) -------
# The analysis pipeline extracts text natively for text-layer PDFs and office/plaintext types and skips
# the OCR backend, so those documents never got a terminal OCR state and lingered 'pending'. finalize_*
# gives them a truthful terminal state without redoing OCR.

from app.services.document_ocr import finalize_document_ocr_state  # noqa: E402


def test_finalize_marks_non_ocr_type_unsupported(tmp_path):
    did = _doc(tmp_path, f"{_TAG} notes.docx", sha="d" * 64)     # office type, no OCR row
    assert finalize_document_ocr_state(did) == "unsupported"
    assert _ocr(did)["status"] == "unsupported"


def test_finalize_native_text_pdf_completes_and_stores_text(tmp_path, monkeypatch):
    import app.services.document_owner_proposal as prop
    monkeypatch.setattr(prop, "_pdf_text", lambda p: "a genuine selectable native text layer with content")
    did = _doc(tmp_path, f"{_TAG} native.pdf", sha="n" * 64)
    assert finalize_document_ocr_state(did) == "completed"
    row = _ocr(did)
    assert row["status"] == "completed" and "native text layer" in row["text"]
    assert "pdf-text-layer" in row["engine"]                     # truthful: text layer, no OCR needed


def test_finalize_ocr_supported_without_text_marks_failed(tmp_path, monkeypatch):
    import app.services.document_owner_proposal as prop
    monkeypatch.setattr(prop, "_pdf_text", lambda p: "")         # no usable native text
    did = _doc(tmp_path, f"{_TAG} scan.pdf", sha="s" * 64)
    assert finalize_document_ocr_state(did) == "failed"          # genuine gap, retryable — not 'completed'
    assert _ocr(did)["status"] == "failed"


def test_finalize_leaves_terminal_states_untouched(tmp_path):
    from app.services.document_ocr import _write_state
    did = _doc(tmp_path, f"{_TAG} done.pdf", sha="x" * 64)
    _write_state(did, status="completed", text="existing text", engine_name="tesseract 5",
                 source_hash="x" * 64, completed=True)
    assert finalize_document_ocr_state(did) == "completed"
    assert _ocr(did)["text"] == "existing text"                 # not rewritten

    did2 = _doc(tmp_path, f"{_TAG} bad.pdf", sha="y" * 64)
    _write_state(did2, status="failed", last_error="boom", source_hash="y" * 64, bump_attempt=True)
    assert finalize_document_ocr_state(did2) == "failed"        # failed stays failed (identifiable)
    assert _ocr(did2)["last_error"] == "boom"


def test_finalize_is_idempotent(tmp_path):
    did = _doc(tmp_path, f"{_TAG} sheet.xlsx", sha="z" * 64)
    assert finalize_document_ocr_state(did) == "unsupported"
    assert finalize_document_ocr_state(did) == "unsupported"    # second call is a no-op


# --- OCR backend unavailable must record a truthful RETRYABLE state, not a silent no-state -------------
# Root cause of the pilot's 25 "no OCR state" docs: _live_ocr swallowed build_production_extractor()'s
# OcrBackendUnavailable and returned "" WITHOUT writing any document_ocr row, so scanned/image documents
# were left stateless and later mislabeled. They must instead be recorded failed (retryable).

def test_live_ocr_records_failed_state_when_backend_unavailable(tmp_path, monkeypatch):
    from app.services.document_owner_proposal import _live_ocr
    _no_engine(monkeypatch)                                    # deterministic: no reachable engine
    did = _doc(tmp_path, f"{_TAG} scan.png", sha="p" * 64)      # image genuinely needs OCR
    with engine.connect() as conn:
        # The availability check runs in-process BEFORE any subprocess isolation, so the patch above
        # governs the outcome on every host.
        text = _live_ocr(conn, did)
    assert text == ""                                          # still fails safe (ingestion not blocked)
    row = _ocr(did)
    assert row is not None and row["status"] == "failed"       # NOT silently stateless
    assert "OCR backend unavailable" in (row["last_error"] or "")


def test_backend_unavailable_state_is_retryable_and_completes(tmp_path):
    from app.services.document_ocr import record_ocr_unavailable
    did = _doc(tmp_path, f"{_TAG} retry.pdf", sha="q" * 64)
    record_ocr_unavailable(did, "no engine on host")
    assert _ocr(did)["status"] == "failed"                     # recorded, retryable
    deps, _ = _deps(pdf_texts=["the engine is now installed and this text is extracted"])
    s = ocr.run_ocr(isolate=False, document_ids=[did], extractor=build_extractor(deps), mode="retry")
    assert s["completed"] == 1 and _ocr(did)["status"] == "completed"   # retry succeeds once engine present


# --- production CLI must build + pass the real extractor (never run_ocr's always-raising stub) ---------
# Bug: document_ocr.main() called run_ocr() with no extractor, so run_ocr used default_extractor which
# ALWAYS raises OcrBackendUnavailable -> `--mode retry` failed every candidate even with Tesseract/Poppler
# installed. main() must build_production_extractor() and pass it in.

def test_cli_builds_and_passes_production_extractor(tmp_path, monkeypatch):
    # A failed candidate must transition to completed when the CLI wires the real (mocked) extractor.
    # Pinned to the diagnostics in-process mode (OCR_SUBPROCESS_ISOLATION=0): the CLI now isolates BY
    # DEFAULT, which extracts in a spawned child that rebuilds the backend from the production factory ref
    # and so cannot see this monkeypatched in-process fake. This test asserts the original bug fix — the
    # CLI builds + passes the REAL backend, never run_ocr's always-raising default_extractor stub — which
    # is only observable in-process. The now-default ISOLATED wiring (isolate=True + production factory
    # ref) is covered by tests/test_document_ocr_cli_isolation.py.
    monkeypatch.setenv("OCR_SUBPROCESS_ISOLATION", "0")
    did = _doc(tmp_path, f"{_TAG} clifix.pdf", sha="c1" * 32)
    from app.services.document_ocr import record_ocr_unavailable
    record_ocr_unavailable(did, "was unavailable before the engine was installed")
    assert _ocr(did)["status"] == "failed"

    deps, _ = _deps(pdf_texts=["the CLI now uses the real backend and extracts this text"])
    built = {"n": 0}

    def fake_build():
        built["n"] += 1
        return build_extractor(deps)
    # Patch where main() imports it from (app.services.ocr_backend.build_production_extractor)
    monkeypatch.setattr("app.services.ocr_backend.build_production_extractor", fake_build)

    ocr.main(["--mode", "retry"])
    assert built["n"] == 1                                      # CLI built the production extractor once
    assert _ocr(did)["status"] == "completed"                  # failed candidate -> completed via real backend
    assert _ocr(did)["text"]                                    # NOT default_extractor (which always raises)


def test_cli_reports_backend_config_error_and_does_not_use_default_extractor(monkeypatch, capsys):
    calls = {"run_ocr": 0}
    real_run = ocr.run_ocr

    def spy_run(*a, **k):
        calls["run_ocr"] += 1
        return real_run(*a, **k)
    monkeypatch.setattr(ocr, "run_ocr", spy_run)

    def boom():
        raise OcrBackendUnavailable("Tesseract not on PATH and TESSERACT_CMD unset")
    monkeypatch.setattr("app.services.ocr_backend.build_production_extractor", boom)

    rc = ocr.main(["--mode", "retry"])
    out = capsys.readouterr().out
    assert rc == 2 and "OCR backend not available" in out       # clear config error
    assert "TESSERACT_CMD" in out
    assert calls["run_ocr"] == 0                                # never ran candidates through the stub


# --- PDF whose text layer is unreadable by pypdf (e.g. "Cannot find Root object") falls back to render --
# Doc 29184 Vanguard: pypdf raised and aborted the whole document. Poppler can often still render it, so
# _extract_pdf now falls back to full-page render + OCR instead of failing.

def test_pdf_text_layer_unreadable_falls_back_to_render_ocr(tmp_path):
    def bad_texts(_p):
        raise Exception("Cannot find Root object; is this really a PDF?")
    deps = OcrDeps(bad_texts, lambda p, i: f"pg-{i}", lambda p: [], lambda img: f"ocr[{img}]",
                   lambda: "5.4.0", pdf_render_all=lambda p: ["imgA", "imgB"])
    ex = build_extractor(deps)
    f = _file(tmp_path, "vanguard.pdf")
    r = ex(_row(f"{_TAG} Vanguard Change form.pdf", f), f)
    assert r["page_count"] == 2 and "ocr[imgA]" in r["text"] and "ocr[imgB]" in r["text"]
    assert "rendered" in r["engine"] and "text layer unreadable" in r["engine"]


def test_unreadable_pdf_without_render_fallback_raises(tmp_path):
    def bad_texts(_p):
        raise Exception("Cannot find Root object")
    deps = OcrDeps(bad_texts, lambda p, i: None, lambda p: [], lambda img: "", lambda: "5.4.0")  # no render_all
    ex = build_extractor(deps)
    f = _file(tmp_path, "corrupt.pdf")
    with pytest.raises(Exception):                              # no fallback -> recorded failed by run_ocr
        ex(_row(f"{_TAG} corrupt.pdf", f), f)


def test_genuinely_corrupt_pdf_render_also_fails_and_propagates(tmp_path):
    def bad_texts(_p):
        raise Exception("Cannot find Root object")

    def bad_render(_p):
        raise Exception("poppler: Document base stream is not seekable / PDF file is damaged")
    deps = OcrDeps(bad_texts, lambda p, i: None, lambda p: [], lambda img: "", lambda: "5.4.0",
                   pdf_render_all=bad_render)
    ex = build_extractor(deps)
    f = _file(tmp_path, "damaged.pdf")
    with pytest.raises(Exception):                              # genuinely unreadable -> truthful failure
        ex(_row(f"{_TAG} damaged.pdf", f), f)


# --- run_ocr progress telemetry (opt-in sink) ---------------------------------------------------------

def test_run_ocr_emits_progress_telemetry(tmp_path):
    ids = [_doc(tmp_path, f"{_TAG} p{i}.pdf", sha=(f"{i}e" * 32)[:64]) for i in range(3)]

    def extractor(row, path):
        if "p1" in row["original_name"]:
            from app.services.document_ocr import OcrTimeout
            raise OcrTimeout("page 1/1 timed out")
        return {"text": f"text {row['id']}", "engine": "tesseract 5", "page_count": 1}
    lines = []
    s = ocr.run_ocr(isolate=False, document_ids=ids, extractor=extractor, mode="reprocess",
                    progress=lines.append, report_every=1)
    assert s["completed"] == 2 and s["timed_out"] == 1
    tallies = [ln for ln in lines if ln.startswith("[ocr:reprocess]")]
    assert tallies, "expected telemetry lines"
    final = tallies[-1]
    # comprehensive fields present, mapped from OCR outcomes
    for token in ("3/3 (100.0%)", "elapsed=", "rate=", "eta=", "completed=2", "timed_out=1", "reused="):
        assert token in final, token


def test_run_ocr_no_progress_sink_is_silent_and_unchanged(tmp_path):
    did = _doc(tmp_path, f"{_TAG} silent.pdf", sha="ab" * 32)
    deps, _ = _deps(pdf_texts=["real selectable content for the silent run"])
    s = ocr.run_ocr(isolate=False, document_ids=[did], extractor=build_extractor(deps))   # no progress= -> no telemetry
    assert s["completed"] == 1                                              # behavior identical to before
