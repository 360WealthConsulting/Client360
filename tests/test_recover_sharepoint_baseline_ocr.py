"""Focused tests for the scoped SharePoint baseline OCR recovery CLI.

Covers: exact scoping (only the verified population is selected), id ordering, the exact-count execution
gate (both --expect-count and the live count must equal EXPECTED_COUNT), read-only --check behavior, and
delegation to the existing ``microsoft_ingestion._ocr_documents`` path. The EXPECTED_COUNT is monkeypatched
to a small value so the gate/delegation can be exercised without materializing 17,448 rows.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import delete, func, insert, select

from app.db import documents, engine
from scripts import recover_sharepoint_baseline_ocr as rec

_EDT = rec.CREATED_FROM.tzinfo                              # -04:00, the verified window's offset
_IN_WINDOW = datetime(2026, 8, 17, 12, 0, 0, tzinfo=_EDT)  # inside [11:00, 15:00)
_BEFORE_WINDOW = datetime(2026, 8, 17, 10, 0, 0, tzinfo=_EDT)
_AFTER_WINDOW = datetime(2026, 8, 17, 15, 30, 0, tzinfo=_EDT)


@pytest.fixture(autouse=True)
def _cleanup():
    with engine.connect() as c:
        high = c.execute(select(func.max(documents.c.id))).scalar() or 0
    yield
    with engine.begin() as c:
        c.execute(delete(documents).where(documents.c.id > high))


def _doc(*, uploaded_by="SharePoint Sync", created_at=_IN_WINDOW):
    sfx = uuid.uuid4().hex
    with engine.begin() as c:
        return c.execute(insert(documents).values(
            original_name="scan.pdf", stored_name=f"s-{sfx}", storage_provider="Client360 Local",
            storage_path=f"/x/{sfx}", size_bytes=1, sha256=(sfx + sfx)[:64], status="active",
            archived=False, uploaded_by=uploaded_by, created_at=created_at).returning(documents.c.id)
        ).scalar_one()


# --- exact scoping -----------------------------------------------------------

def test_scope_selects_only_the_verified_population():
    want = [_doc(), _doc(), _doc()]                                   # SharePoint Sync, in window → included
    _doc(uploaded_by="Drake Sync")                                    # wrong source → excluded
    _doc(uploaded_by="TaxDome Drive Sync")                            # wrong source → excluded
    _doc(created_at=_BEFORE_WINDOW)                                   # before window → excluded
    _doc(created_at=_AFTER_WINDOW)                                    # at/after window end → excluded

    selected = set(rec.scope_ids())
    assert selected == set(want)                                     # exactly the SharePoint-in-window rows


def test_window_is_half_open_lower_inclusive_upper_exclusive():
    at_lower = _doc(created_at=rec.CREATED_FROM)                      # >= FROM → included
    at_upper = _doc(created_at=rec.CREATED_TO)                        # <  TO  → excluded (exclusive)
    ids = rec.scope_ids()
    assert at_lower in ids and at_upper not in ids


# --- ordering ----------------------------------------------------------------

def test_ids_are_ordered_by_document_id():
    for _ in range(5):
        _doc()
    ids = rec.scope_ids()
    assert ids == sorted(ids)


# --- stats / --check (read-only) --------------------------------------------

def test_scope_stats_reports_count_and_bounds():
    ids = sorted(_doc() for _ in range(3))
    st = rec.scope_stats()
    assert st["count"] == 3
    assert st["min_id"] == ids[0] and st["max_id"] == ids[-1]
    assert st["min_created_at"] is not None and st["max_created_at"] is not None


def test_check_mode_is_read_only_and_never_runs_ocr(monkeypatch, capsys):
    _doc()
    called = {"n": 0}
    monkeypatch.setattr("app.services.microsoft_ingestion._ocr_documents",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    rc = rec.main(["--check"])
    assert rc == 0 and called["n"] == 0                              # no OCR path invoked
    assert "scoped population" in capsys.readouterr().out.lower()


# --- exact-count execution gate ---------------------------------------------

def test_run_refuses_when_expect_count_flag_is_wrong(monkeypatch):
    monkeypatch.setattr(rec, "EXPECTED_COUNT", 3)
    for _ in range(3):
        _doc()
    called = {"n": 0}
    monkeypatch.setattr("app.services.microsoft_ingestion._ocr_documents",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    rc = rec.main(["--run", "--expect-count", "999"])               # confirmation flag mismatched
    assert rc == 2 and called["n"] == 0


def test_run_refuses_when_live_count_is_not_exact(monkeypatch):
    monkeypatch.setattr(rec, "EXPECTED_COUNT", 3)
    _doc()
    _doc()                                                          # only 2 present, expected 3
    called = {"n": 0}
    monkeypatch.setattr("app.services.microsoft_ingestion._ocr_documents",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    rc = rec.main(["--run", "--expect-count", "3"])
    assert rc == 2 and called["n"] == 0                             # count gate blocks execution


# --- delegation to the existing baseline OCR path ---------------------------

def test_run_delegates_exact_ordered_ids_to_ocr_documents(monkeypatch, capsys):
    monkeypatch.setattr(rec, "EXPECTED_COUNT", 3)
    ids = sorted(_doc() for _ in range(3))
    _doc(uploaded_by="Drake Sync")                                   # noise that must NOT be passed

    captured = {}

    def _fake_ocr_documents(document_ids, *, progress=None):
        captured["ids"] = list(document_ids)
        captured["progress"] = progress
        return {"ocr_analyzed": 3, "ocr_failed": 0, "ocr_timed_out": 0, "ocr_other": 0}

    monkeypatch.setattr("app.services.microsoft_ingestion._ocr_documents", _fake_ocr_documents)
    rc = rec.main(["--run", "--expect-count", "3"])
    assert rc == 0
    assert captured["ids"] == ids                                   # exactly the scoped ids, in id order
    assert callable(captured["progress"])                           # progress callback is wired through
    out = capsys.readouterr().out
    assert "SharePoint baseline recovery: 3 docs" in out            # startup banner
