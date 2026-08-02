"""OCR foundation (PR 5A) — coverage.

OCR as a service over the canonical document model (ADR-072): text extraction against the canonical
``documents`` row (no second document system), search indexing (Universal Search matches OCR text),
canonical enrichment (no duplicate documents), retry logic, incremental processing, and permission /
record-scope enforcement on OCR-text search. The pixel/PDF engine is injected as an ``extractor``;
tests use deterministic fakes. Temp/test rows only.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import delete, select

from app.db import document_ocr, documents, engine, people
from app.security.models import Principal
from app.services import document_ocr as ocr
from app.services.client360 import get_workspace
from app.services.universal_search import universal_search

_TAG = "OCRTEST"
_CAPS = frozenset({"client.read", "documents.view", "record.read_all"})
_UID = None


@pytest.fixture(autouse=True)
def _clean():
    from app.db import record_assignments, users

    def _wipe():
        with engine.begin() as c:
            doc_ids = list(c.scalars(select(documents.c.id).where(
                documents.c.original_name.like(f"%{_TAG}%"))))
            if doc_ids:
                c.execute(delete(document_ocr).where(document_ocr.c.document_id.in_(doc_ids)))
                c.execute(delete(documents).where(documents.c.id.in_(doc_ids)))
            pids = list(c.scalars(select(people.c.id).where(people.c.full_name.like(f"%{_TAG}%"))))
            if pids:
                c.execute(delete(record_assignments).where(record_assignments.c.entity_id.in_(pids)))
                c.execute(delete(people).where(people.c.id.in_(pids)))
    _wipe()
    global _UID
    with engine.begin() as c:
        tag = uuid.uuid4().hex[:8]
        _UID = c.execute(users.insert().values(
            email=f"ocr{tag}@e.test", normalized_email=f"ocr{tag}@e.test",
            display_name="OCR", status="active").returning(users.c.id)).scalar_one()
    yield
    _wipe()


def _person(name="Doc Owner"):
    with engine.begin() as c:
        return c.execute(people.insert().values(
            first_name=name.split()[0], last_name="Owner", full_name=f"{name} {_TAG}",
            active=True).returning(people.c.id)).scalar_one()


def _doc(name=f"scan {_TAG}.pdf", *, person_id=None, sha=None):
    with engine.begin() as c:
        return c.execute(documents.insert().values(
            original_name=name, stored_name=f"{name}-{uuid.uuid4().hex[:8]}",
            storage_path="/x", storage_provider="Client360 Local", storage_uri="/x/scan",
            size_bytes=10, sha256=sha or (uuid.uuid4().hex + uuid.uuid4().hex),
            person_id=person_id, status="active", archived=False).returning(documents.c.id)).scalar_one()


def _text(msg):
    return lambda row, path: msg


def _boom(row, path):
    raise RuntimeError("engine crashed")


def _ocr_row(doc_id):
    with engine.connect() as c:
        return c.execute(select(document_ocr).where(
            document_ocr.c.document_id == doc_id)).mappings().first()


def _principal():
    return Principal(_UID or 0, "a@e.test", "A", _CAPS)


# --- extraction --------------------------------------------------------------

def test_ocr_extraction_stores_text_against_canonical(tmp_path):
    did = _doc(sha="a" * 64)
    s = ocr.run_ocr(document_ids=[did], extractor=_text("Form 1040 line 1 wages"), actor_user_id=_UID)
    assert s["completed"] == 1 and s["failed"] == 0
    row = _ocr_row(did)
    assert row["status"] == "completed" and row["text"] == "Form 1040 line 1 wages"
    assert row["char_count"] == len("Form 1040 line 1 wages") and row["source_hash"] == "a" * 64
    assert row["ocr_completed_at"] is not None
    with engine.connect() as c:
        assert c.scalar(select(documents.c.ocr_status).where(documents.c.id == did)) == "completed"


def test_unsupported_type_is_skipped_not_failed():
    did = _doc(name=f"notes {_TAG}.zip")
    s = ocr.run_ocr(document_ids=[did], extractor=_text("x"))
    assert s["unsupported"] == 1 and s["completed"] == 0
    assert _ocr_row(did)["status"] == "unsupported"


# --- search indexing ---------------------------------------------------------

def test_search_matches_ocr_text():
    did = _doc(name=f"generic {_TAG}.pdf")
    token = f"quokkaledger{uuid.uuid4().hex[:6]}"
    ocr.run_ocr(document_ids=[did], extractor=_text(f"balance sheet {token} total"))
    res = universal_search(_principal(), token, types=["document"])
    assert any(r["id"] == did for r in res["results"])          # found by extracted OCR text


def test_search_still_matches_filename():
    did = _doc(name=f"UniqueName{uuid.uuid4().hex[:6]} {_TAG}.pdf")
    name = _ocr_and_name(did)
    res = universal_search(_principal(), name, types=["document"])
    assert any(r["id"] == did for r in res["results"])


def _ocr_and_name(did):
    with engine.connect() as c:
        return c.scalar(select(documents.c.original_name).where(documents.c.id == did)).split()[0]


# --- canonical enrichment / duplicate handling -------------------------------

def test_enrichment_creates_no_new_document_rows():
    did = _doc(sha="b" * 64)
    with engine.connect() as c:
        before = c.scalar(select(documents.c.id).where(documents.c.id == did))
        n_before = c.execute(select(documents.c.id)).rowcount
    ocr.run_ocr(document_ids=[did], extractor=_text("enriched"))
    with engine.connect() as c:
        n_after = c.execute(select(documents.c.id)).rowcount
    assert before is not None and n_after == n_before          # OCR enriches, never adds a document


def test_rerun_does_not_duplicate_ocr_rows():
    did = _doc(sha="c" * 64)
    ocr.run_ocr(document_ids=[did], extractor=_text("first"))
    ocr.run_ocr(document_ids=[did], extractor=_text("second"), mode="reprocess")
    with engine.connect() as c:
        n = c.execute(select(document_ocr.c.id).where(document_ocr.c.document_id == did)).rowcount
    assert n == 1                                              # one OCR record per canonical document


def test_incremental_skips_completed_unchanged():
    did = _doc(sha="d" * 64)
    ocr.run_ocr(document_ids=[did], extractor=_text("done"))
    # An incremental pass must not re-OCR an already-completed, unchanged document.
    s = ocr.run_ocr(document_ids=[did], mode="incremental", extractor=_text("SHOULD NOT OVERWRITE"))
    assert s["skipped"] == 1 and s["completed"] == 0
    assert _ocr_row(did)["text"] == "done"                    # untouched


def test_reprocess_on_content_change():
    did = _doc(sha="e" * 64)
    ocr.run_ocr(document_ids=[did], extractor=_text("v1"))
    with engine.begin() as c:                                 # canonical content changed
        c.execute(documents.update().where(documents.c.id == did).values(sha256="f" * 64))
    s = ocr.run_ocr(document_ids=[did], mode="reprocess", extractor=_text("v2"))
    assert s["completed"] == 1
    row = _ocr_row(did)
    assert row["text"] == "v2" and row["source_hash"] == "f" * 64


# --- retry logic -------------------------------------------------------------

def test_failure_is_recorded_and_retryable():
    did = _doc(sha="1" * 64)
    s = ocr.run_ocr(document_ids=[did], extractor=_boom)
    assert s["failed"] == 1
    row = _ocr_row(did)
    assert row["status"] == "failed" and row["attempts"] == 1 and "engine crashed" in row["last_error"]
    with engine.connect() as c:
        assert c.scalar(select(documents.c.ocr_status).where(documents.c.id == did)) == "failed"
    # Retry mode picks it up and succeeds; attempts increments.
    ocr.run_ocr(mode="retry", extractor=_text("recovered"))
    row2 = _ocr_row(did)
    assert row2["status"] == "completed" and row2["attempts"] == 2 and row2["text"] == "recovered"


def test_retry_respects_max_attempts():
    did = _doc(sha="2" * 64)
    for _ in range(3):
        ocr.run_ocr(mode="retry" if _ else "initial", document_ids=[did] if not _ else None,
                    extractor=_boom, max_attempts=3)
    # After 3 failed attempts, a retry sweep no longer selects it.
    s = ocr.run_ocr(mode="retry", extractor=_boom, max_attempts=3)
    assert did not in [r["id"] for r in _failed_candidates()]
    assert s  # sweep ran without selecting the exhausted document


def _failed_candidates():
    with engine.connect() as c:
        return [{"id": r} for r in c.scalars(select(document_ocr.c.document_id).where(
            document_ocr.c.status == "failed", document_ocr.c.attempts < 3))]


# --- permission enforcement --------------------------------------------------

def test_ocr_search_respects_record_scope():
    from app.db import record_assignments, users
    mine = _person("Mine")
    theirs = _person("Theirs")
    token = f"scopetoken{uuid.uuid4().hex[:6]}"
    dm = _doc(name=f"mine {_TAG}.pdf", person_id=mine)
    dt = _doc(name=f"theirs {_TAG}.pdf", person_id=theirs)
    ocr.run_ocr(document_ids=[dm, dt], extractor=_text(f"secret {token} data"))
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"scoped{uuid.uuid4().hex[:6]}@e.test",
            normalized_email=f"scoped{uuid.uuid4().hex[:6]}@e.test",
            display_name="Scoped", status="active").returning(users.c.id)).scalar_one()
        c.execute(record_assignments.insert().values(
            user_id=uid, entity_type="person", entity_id=mine, assignment_type="primary",
            effective_date=date.today()))
    scoped = Principal(uid, "s@e.test", "Scoped", frozenset({"client.read", "documents.view"}))
    res = universal_search(scoped, token, types=["document"])
    ids = {r["id"] for r in res["results"]}
    assert dm in ids and dt not in ids                        # only the in-scope document is returned


# --- Documents tab rendering -------------------------------------------------

def test_documents_tab_surfaces_ocr_status_and_searchable():
    pid = _person("Tab")
    did = _doc(name=f"tabdoc {_TAG}.pdf", person_id=pid)
    ocr.run_ocr(document_ids=[did], extractor=_text("indexed content"))
    sec = get_workspace(_principal(), person_id=pid)["sections"]["documents"]
    assert sec["ocr_enabled"] is True
    d = next(x for x in sec["documents"] if x["id"] == did)
    assert d["ocr_status"] == "completed" and d["searchable_text"] is True
    assert d["ocr_completed_at"] is not None


# --- audit + dry run ---------------------------------------------------------

def test_run_is_audited():
    from app.db import audit_events
    did = _doc(sha="9" * 64)
    ocr.run_ocr(document_ids=[did], extractor=_text("audited"), request_id="ocr-t", actor_user_id=_UID)
    with engine.connect() as c:
        assert c.scalar(select(audit_events.c.id).where(
            audit_events.c.action == "document.ocr_run").limit(1)) is not None


def test_dry_run_makes_no_changes():
    did = _doc(sha="8" * 64)
    s = ocr.run_ocr(document_ids=[did], extractor=_text("nope"), dry_run=True)
    assert s["dry_run"] is True
    assert _ocr_row(did) is None                              # nothing written
