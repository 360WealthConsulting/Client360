"""Phase 1C — the shipped ``python -m app.services.document_ocr`` CLI must run production OCR under
subprocess isolation BY DEFAULT.

Before this change the CLI called ``run_ocr`` with the default ``isolate=False``, so it ran the real
extraction backend IN-PROCESS with no wall-clock timeout — a pathological PDF/image could wedge the CLI
parent process indefinitely. These tests prove:
  * the default (and explicit ``=1``) CLI path passes ``isolate=True`` + the production factory ref, so a
    document is extracted in a killable child process with the real hard-cap/stall watchdog;
  * candidate selection / scope are unchanged (still a firm-wide sweep by ``mode``);
  * the non-isolated in-process path is DIAGNOSTICS-ONLY — reachable only by deliberately setting
    ``OCR_SUBPROCESS_ISOLATION=0`` (never by accident) and it warns loudly;
  * the exact (isolate, factory_ref) the CLI now passes really does terminate a wedged child and continue.

The child-kill / stall-watchdog / no-orphan MECHANICS are proven at the run_document boundary in
``tests/test_ocr_isolation.py`` (``test_no_orphan_process_tree_survives`` etc.); this module proves the
CLI entrypoint is wired into that isolation and inherits it.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, func, insert, select

from app.db import document_ocr, documents, engine
from app.jobs import ocr_runner
from app.services import document_ocr as doc_ocr

_DBL = "tests.ocr_doubles"
# A stand-in for build_production_extractor(): returns an extractor callable (value irrelevant — the
# isolated path rebuilds the extractor in the child from the factory ref, never from this object).
_DUMMY_EXTRACTOR = lambda: (lambda row, path: {"text": "x", "engine": "fake", "page_count": 1})  # noqa: E731


@pytest.fixture(autouse=True)
def _cleanup_created_docs():
    """Delete every document (+ OCR row) created here so leftover state cannot leak into other suites."""
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
        return c.execute(select(document_ocr.c.status, document_ocr.c.attempts).where(
            document_ocr.c.document_id == doc_id)).mappings().one_or_none()


def _capture_run_ocr(monkeypatch):
    """Replace run_ocr with a recorder so we can assert exactly how the CLI wires isolation, without
    touching the DB or spawning children."""
    captured = {}

    def _fake(**kw):
        captured.update(kw)
        summary = doc_ocr._new_summary(kw.get("mode"), kw.get("dry_run", False))
        summary["status"] = "completed"
        return summary

    monkeypatch.setattr(doc_ocr, "run_ocr", _fake)
    monkeypatch.setattr("app.services.ocr_backend.build_production_extractor", _DUMMY_EXTRACTOR)
    return captured


# --- 1. default + explicit-enabled CLI paths use subprocess isolation --------

def test_cli_default_uses_subprocess_isolation(monkeypatch):
    monkeypatch.delenv("OCR_SUBPROCESS_ISOLATION", raising=False)   # unset == default == isolated
    captured = _capture_run_ocr(monkeypatch)
    rc = doc_ocr.main(["--mode", "incremental"])
    assert rc == 0
    assert captured["isolate"] is True
    assert captured["factory_ref"] == ocr_runner._PRODUCTION_FACTORY   # reuses the runner's constant
    # Candidate selection + scope are unchanged: firm-wide sweep by mode, no document_ids narrowing.
    assert captured["mode"] == "incremental"
    assert captured.get("document_ids") is None


def test_cli_explicit_enabled_uses_isolation(monkeypatch):
    monkeypatch.setenv("OCR_SUBPROCESS_ISOLATION", "1")
    captured = _capture_run_ocr(monkeypatch)
    assert doc_ocr.main(["--mode", "reprocess", "--batch-size", "10"]) == 0
    assert captured["isolate"] is True
    assert captured["factory_ref"] == ocr_runner._PRODUCTION_FACTORY
    assert captured["mode"] == "reprocess" and captured["batch_size"] == 10   # args preserved


# --- 2. the non-isolated path is diagnostics-only and requires explicit opt-in

def test_cli_non_isolated_requires_explicit_optout(monkeypatch, capsys):
    monkeypatch.setenv("OCR_SUBPROCESS_ISOLATION", "0")            # deliberate diagnostics opt-out
    captured = _capture_run_ocr(monkeypatch)
    rc = doc_ocr.main(["--mode", "incremental"])
    assert rc == 0
    assert captured["isolate"] is False and captured["factory_ref"] is None
    out = capsys.readouterr().out
    # The opt-out is loud and unmistakable — it cannot be selected silently or by accident.
    assert "DISABLED" in out and "DIAGNOSTICS" in out and "OCR_SUBPROCESS_ISOLATION=0" in out


def test_cli_does_not_warn_when_isolated(monkeypatch, capsys):
    monkeypatch.delenv("OCR_SUBPROCESS_ISOLATION", raising=False)
    _capture_run_ocr(monkeypatch)
    doc_ocr.main(["--mode", "incremental"])
    out = capsys.readouterr().out
    assert "DISABLED" not in out and "DIAGNOSTICS" not in out    # no scary warning on the safe path


# --- 3. the (isolate, factory_ref) the CLI passes really terminates a wedge ---

def test_default_production_wiring_terminates_wedged_child_and_continues(monkeypatch):
    # Prove the EXACT pair the CLI now passes by default (isolate=True, factory_ref=<production factory>)
    # kills a wedged child by wall clock and lets the batch continue — using a fake 'production' factory
    # that hangs on documents whose name contains HANG and succeeds otherwise. Scoped to document_ids so
    # it is deterministic in the shared test DB (does not sweep other suites' documents).
    monkeypatch.setattr(ocr_runner, "_PRODUCTION_FACTORY", f"{_DBL}.selective_factory")
    hang = _doc("cliHANG.pdf")
    ok = _doc("cliOK.pdf")
    summary = doc_ocr.run_ocr(mode="reprocess", document_ids=[hang, ok],
                              isolate=True, factory_ref=ocr_runner._PRODUCTION_FACTORY,
                              hard_timeout=2, stall_timeout=8)
    assert summary["timed_out"] == 1 and summary["completed"] == 1   # wedge timed out, next completed
    assert _ocr_state(hang)["status"] == "timed_out"
    assert _ocr_state(hang)["attempts"] >= 1                         # recorded distinctly, retryable
    assert _ocr_state(ok)["status"] == "completed"                   # caller continued past the wedge
