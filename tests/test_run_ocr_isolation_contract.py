"""Phase 1C (fail-closed refinement) — run_ocr()'s isolation API is safe by default.

Before this refinement ``run_ocr`` defaulted to ``isolate=False``: a new production caller could invoke
``run_ocr(...)`` (or pass the real backend as an in-process ``extractor=``) without stating isolation and
silently reintroduce an unkillable OCR path with no wall-clock timeout. The contract is now fail-closed —
omitting the isolation argument is REFUSED. These tests prove:

  * a bare / default production-style ``run_ocr()`` cannot accidentally run OCR in-process (it raises);
  * passing an in-process ``extractor`` object does NOT excuse the choice (the real-backend footgun);
  * ``isolate=True`` still requires a ``factory_ref`` (the child rebuilds the extractor across spawn);
  * an omitted choice WITH a ``factory_ref`` resolves to isolated (the safe default when possible);
  * explicit ``isolate=False`` in-process execution is preserved for injected fakes;
  * ``extract_text()`` is safe by default (isolated production path) and only runs in-process with an
    explicitly injected extractor;
  * a STATIC scan of every ``run_ocr`` call in ``app/`` requires an explicit isolation decision, so a new
    production caller cannot bypass the policy even without executing that path.
"""
from __future__ import annotations

import ast
import pathlib
import uuid

import pytest
from sqlalchemy import delete, func, insert, select

from app.db import document_ocr, documents, engine
from app.jobs import ocr_runner
from app.services import document_ocr as doc_ocr
from app.services.ocr_exceptions import OcrBackendUnavailable, OcrIsolationError

_APP_ROOT = pathlib.Path(doc_ocr.__file__).resolve().parents[1]   # .../app


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


def _ocr_row(did):
    with engine.connect() as c:
        return c.execute(select(document_ocr.c.status, document_ocr.c.text).where(
            document_ocr.c.document_id == did)).mappings().one_or_none()


# --- 1. omission is fail-closed (the core regression) ------------------------

@pytest.mark.parametrize("kwargs", [
    {"mode": "incremental"},                                    # bare, production-style
    {"mode": "reprocess", "document_ids": [1]},                 # targeted, no isolation stated
    {"mode": "reprocess", "document_ids": [1],
     "extractor": (lambda row, path: {"text": "x"})},           # an extractor object does NOT excuse it
])
def test_run_ocr_refuses_omitted_isolation(kwargs):
    # The refusal happens before any candidate is selected — no DB access, no document touched.
    with pytest.raises(OcrIsolationError):
        doc_ocr.run_ocr(**kwargs)


def test_isolate_true_requires_factory_ref():
    with pytest.raises(OcrIsolationError):
        doc_ocr.run_ocr(mode="reprocess", document_ids=[1], isolate=True)   # no factory_ref


def test_resolver_matrix():
    r = doc_ocr._resolve_isolation
    assert r(doc_ocr._ISOLATE_UNSET, "pkg.factory") is True     # omitted + factory -> isolated (safe)
    assert r(False, None) is False                              # explicit in-process
    assert r(True, "pkg.factory") is True                       # explicit isolated
    with pytest.raises(OcrIsolationError):
        r(doc_ocr._ISOLATE_UNSET, None)                         # omitted + no factory -> refuse
    with pytest.raises(OcrIsolationError):
        r(True, None)                                           # isolate=True needs a factory
    assert issubclass(OcrIsolationError, ValueError)            # broad handlers still catch it


# --- 2. explicit in-process is preserved for injected fakes ------------------

def test_explicit_in_process_still_runs(monkeypatch):
    did = _doc("contractOK.pdf")
    summary = doc_ocr.run_ocr(mode="reprocess", document_ids=[did], isolate=False,
                              extractor=lambda row, path: {"text": "hello", "engine": "fake"})
    assert summary["completed"] == 1
    assert _ocr_row(did)["status"] == "completed" and _ocr_row(did)["text"] == "hello"


# --- 3. extract_text(): safe by default, explicit in-process for fakes -------

def test_extract_text_is_isolated_by_default(monkeypatch):
    # No injected extractor -> builds the production backend and routes through subprocess isolation.
    monkeypatch.delenv("OCR_SUBPROCESS_ISOLATION", raising=False)     # default == isolated
    monkeypatch.setattr("app.services.ocr_backend.build_production_extractor",
                        lambda: (lambda row, path: {"text": "x"}))
    captured = {}
    monkeypatch.setattr(doc_ocr, "run_ocr", lambda **kw: captured.update(kw) or {})
    doc_ocr.extract_text(4242)
    assert captured["isolate"] is True
    assert captured["factory_ref"] == ocr_runner._PRODUCTION_FACTORY
    assert captured["document_ids"] == [4242] and captured["mode"] == "reprocess"


def test_extract_text_propagates_backend_unavailable_not_silent_stub():
    # In CI no OCR backend is installed: the default path must surface the real backend error (proving it
    # attempts the production/isolated path), never silently run run_ocr's always-raising default_extractor.
    with pytest.raises(OcrBackendUnavailable):
        doc_ocr.extract_text(_doc("needsEngine.pdf"))


def test_extract_text_with_injected_extractor_runs_in_process():
    did = _doc("injected.pdf")
    result = doc_ocr.extract_text(did, extractor=lambda row, path: {"text": "native", "engine": "fake"})
    assert result["completed"] == 1
    assert _ocr_row(did)["status"] == "completed" and _ocr_row(did)["text"] == "native"


# --- 4. STATIC contract: every run_ocr call in app/ states an isolation choice

def _run_ocr_call_sites():
    """Yield (relpath, lineno) for every ``run_ocr(...)`` CALL under app/ that omits BOTH ``isolate`` and
    ``factory_ref`` — i.e. would silently take the in-process path under the pre-refinement default."""
    offenders = []
    for path in sorted(_APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (func.attr if isinstance(func, ast.Attribute)
                    else func.id if isinstance(func, ast.Name) else None)
            if name != "run_ocr":
                continue
            keys = {kw.arg for kw in node.keywords}
            if "isolate" not in keys and "factory_ref" not in keys:
                offenders.append((path.relative_to(_APP_ROOT.parent).as_posix(), node.lineno))
    return offenders


def test_no_app_caller_bypasses_isolation_policy():
    offenders = _run_ocr_call_sites()
    assert offenders == [], (
        "Every run_ocr() call in app/ must state an explicit isolation decision (isolate= or "
        f"factory_ref=). Un-annotated call sites that could silently run OCR in-process: {offenders}")
