"""Parallel OCR end to end through REAL worker processes: claim_batch -> worker_loop -> run_ocr.

``tests/test_ocr_parallel_claims.py`` proves the claim primitives in-process. This file proves the
whole path with the process model production would actually use: ``run_parallel`` spawns N workers,
each claims through ``claim_batch``, runs ``run_ocr`` over exactly its claimed ids, and completes its
claims. The extractor crosses the spawn boundary as a dotted reference (``tests.ocr_doubles``), the
same way ``build_production_extractor`` does, so nothing here depends on a picklable closure.

Everything runs against the disposable test database on a small tagged document set, and every claim
is scoped with ``document_ids`` so a populated database cannot feed these workers somebody else's
documents.
"""
import time
import uuid

import pytest
from sqlalchemy import delete, select, text

from app.db import document_ocr, documents, engine
from app.jobs import ocr_claims, ocr_parallel

_TAG = "OCRPARINT"
_DBL = "tests.ocr_doubles"


@pytest.fixture(autouse=True)
def _clean():
    def _wipe():
        with engine.begin() as c:
            ids = list(c.scalars(select(documents.c.id).where(
                documents.c.original_name.like(f"%{_TAG}%"))))
            if ids:
                c.execute(text("DELETE FROM ocr_document_claims WHERE document_id = ANY(:i)"),
                          {"i": ids})
                c.execute(delete(document_ocr).where(document_ocr.c.document_id.in_(ids)))
                c.execute(delete(documents).where(documents.c.id.in_(ids)))
    _wipe()
    yield
    _wipe()


def _docs(n, *, marker=""):
    ids = []
    with engine.begin() as c:
        for i in range(n):
            ids.append(c.execute(documents.insert().values(
                original_name=f"{_TAG} {marker}doc {i}.pdf",
                stored_name=f"{_TAG}-{uuid.uuid4().hex[:8]}",
                storage_path="/x", storage_provider="Client360 Local", storage_uri="/x",
                size_bytes=10, sha256=uuid.uuid4().hex + uuid.uuid4().hex, status="active",
                archived=False).returning(documents.c.id)).scalar_one())
    return ids


def _claims_for(ids):
    with engine.connect() as c:
        return [dict(r) for r in c.execute(text("""
            SELECT id, document_id, worker_id, state, claim_seq
              FROM ocr_document_claims WHERE document_id = ANY(:i) ORDER BY document_id"""),
            {"i": list(ids)}).mappings()]


def _ocr_rows(ids):
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(document_ocr.c.document_id, document_ocr.c.status, document_ocr.c.text,
                   document_ocr.c.attempts)
            .where(document_ocr.c.document_id.in_(list(ids)))).mappings()]


def _run(workers, ids, *, factory=f"{_DBL}.ok_factory", batch=3, mode="initial"):
    return ocr_parallel.run_parallel(workers=workers, mode=mode, batch=batch,
                                     factory_ref=factory, document_ids=list(ids))


# --- the main end-to-end, at 1, 2 and 4 workers ---------------------------------------------------

@pytest.mark.parametrize("workers", [1, 2, 4])
def test_every_eligible_document_is_claimed_and_processed_exactly_once(workers):
    pool = _docs(24)
    result = _run(workers, pool)

    assert result["status"] == "completed", result
    assert result["workers"] == workers

    # Exactly one OCR row per document, all completed. A second worker touching the same document
    # would either add a row or bump attempts past 1.
    rows = _ocr_rows(pool)
    assert len(rows) == 24, f"expected one OCR row per document, got {len(rows)}"
    assert {r["status"] for r in rows} == {"completed"}
    # A successful extraction bumps attempts to exactly 1 (document_ocr._write_state). Two workers
    # having processed the same document would show 2.
    assert {r["attempts"] for r in rows} == {1}, "attempts > 1 means a document was processed twice"

    # Exactly one claim per document, all done, each taken once (claim_seq never re-incremented).
    claims = _claims_for(pool)
    assert len(claims) == 24
    assert {c["state"] for c in claims} == {"done"}
    assert {c["claim_seq"] for c in claims} == {1}, "a re-claim means two workers wanted it"
    assert len({c["document_id"] for c in claims}) == 24

    # The runner's own accounting has to agree with the database.
    assert result["completed"] == 24
    assert result["claimed"] == 24
    assert result["lost_claims"] == 0


def test_the_worker_actually_uses_the_ISOLATED_subprocess_path():
    """Regression: worker_loop passed ``isolate=None`` alongside a factory_ref.

    run_ocr's isolation decision is fail-closed on a SENTINEL, so an explicit None is merely falsy
    and selects the in-process path — which then falls back to ``default_extractor`` and fails every
    document with 'No OCR engine configured on this host'. The in-process unit tests could not see
    it, because they always pass ``factory_ref=None``. Production would have failed every document.

    The proof is that text produced by the factory INSIDE the spawned child reached the database.
    """
    pool = _docs(3)
    result = _run(1, pool)

    assert result["completed"] == 3, "the isolated path must actually run the factory"
    rows = _ocr_rows(pool)
    assert {r["status"] for r in rows} == {"completed"}
    assert all((r["text"] or "").startswith("ok:") for r in rows), (
        "stored text must come from the factory the child rebuilt, not an in-process fallback")

    with engine.connect() as c:
        errs = list(c.scalars(select(document_ocr.c.last_error)
                              .where(document_ocr.c.document_id.in_(pool))))
    assert not any("No OCR engine configured" in (e or "") for e in errs)


def test_the_explicit_in_process_override_still_works():
    """The supported override: no factory_ref plus an injected extractor runs in-process.

    worker_loop must translate that into an EXPLICIT ``isolate=False``, because run_ocr refuses an
    omitted isolation choice with no factory_ref rather than guessing.
    """
    pool = _docs(5)
    totals = ocr_parallel.worker_loop(
        worker_id="in-process", mode="initial", batch=2, document_ids=pool,
        extractor=lambda row, path: {"text": "inproc", "engine": "fake", "page_count": 1},
        factory_ref=None, stop_when_empty=True)

    assert totals["completed"] == 5
    rows = _ocr_rows(pool)
    assert {r["text"] for r in rows} == {"inproc"}
    assert {r["status"] for r in rows} == {"completed"}


def test_a_failing_child_is_reported_and_never_counted_as_success():
    """A worker must not report zero successes while silently swallowing every failure.

    The extractor raises inside the spawned child. Each document has to come back as a recorded
    failure that reaches the aggregated result, not as a quiet no-op.
    """
    pool = _docs(6)
    result = _run(2, pool, factory=f"{_DBL}.boom_factory", batch=2)

    assert result["completed"] == 0
    assert result["failed"] == 6, f"every failure must be counted, got {result}"
    assert result["claimed"] == 6, "documents must still have been claimed"

    rows = _ocr_rows(pool)
    assert len(rows) == 6
    assert {r["status"] for r in rows} == {"failed"}

    with engine.connect() as c:
        errs = list(c.scalars(select(document_ocr.c.last_error)
                              .where(document_ocr.c.document_id.in_(pool))))
    assert all("simulated extraction failure" in (e or "") for e in errs), errs
    assert not any("No OCR engine configured" in (e or "") for e in errs), (
        "a child failure must not be masked as a missing-backend error")

    # Failures are retryable, so the documents stay claimable rather than being lost.
    claims = _claims_for(pool)
    assert {c["state"] for c in claims} == {"done"}
    assert {r["attempts"] for r in _ocr_rows(pool)} == {1}


@pytest.mark.parametrize("workers", [2, 4])
def test_no_document_is_processed_by_two_workers(workers):
    pool = _docs(24)
    result = _run(workers, pool, batch=2)

    # Every worker reports what it claimed; summed, that must equal the pool with no overlap.
    per_worker = [r for r in result["per_worker"] if isinstance(r, dict) and "error" not in r]
    assert per_worker, result["per_worker"]
    assert sum(r["claimed"] for r in per_worker) == 24, "claims must partition the set, not overlap"
    assert sum(r["completed"] for r in per_worker) == 24
    assert all(r["lost_claims"] == 0 for r in per_worker)

    with engine.connect() as c:
        assert ocr_claims.duplicate_live_claims(c) == []


# --- the surrogate key ----------------------------------------------------------------------------

def test_document_id_is_unique_beside_a_surrogate_primary_key():
    """The merge executor reads dependent rows ORDER BY id, so the table needs a surrogate key; the
    mutual-exclusion guarantee has to come from a UNIQUE document_id instead."""
    pool = _docs(4)
    _run(1, pool)
    claims = _claims_for(pool)
    assert len({c["id"] for c in claims}) == 4, "surrogate ids must be distinct"
    assert all(c["id"] is not None for c in claims)

    with engine.connect() as c:
        cols = {r[0] for r in c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='ocr_document_claims'"))}
        assert "id" in cols
        pk = c.execute(text("""
            SELECT a.attname FROM pg_index i
              JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
             WHERE i.indrelid = 'ocr_document_claims'::regclass AND i.indisprimary""")).scalars().all()
        assert list(pk) == ["id"]

    # A second claim row for the same document must be impossible.
    with pytest.raises(Exception):
        with engine.begin() as c:
            c.execute(text("""
                INSERT INTO ocr_document_claims
                       (document_id, worker_id, state, claim_seq, lease_expires_at)
                VALUES (:d, 'intruder', 'claimed', 1, now() + interval '1 hour')"""),
                {"d": pool[0]})


# --- scoping ---------------------------------------------------------------------------------------

def test_document_ids_scope_excludes_every_other_document():
    inside = _docs(6, marker="IN ")
    outside = _docs(6, marker="OUT ")

    _run(2, inside)

    assert len(_ocr_rows(inside)) == 6
    assert _ocr_rows(outside) == [], "a scoped run must not OCR anything outside its set"
    assert _claims_for(outside) == [], "a scoped run must not even claim outside its set"


# --- crash recovery ---------------------------------------------------------------------------------

def test_an_expired_lease_is_reclaimed_and_the_document_still_completes():
    pool = _docs(5)

    # A worker that died holding every document: claims exist, nothing was processed, no release.
    with engine.begin() as c:
        ocr_claims.claim_batch(c, worker_id="crashed-worker", mode="initial", limit=5,
                               document_ids=pool)
    assert len(_claims_for(pool)) == 5
    assert _ocr_rows(pool) == []

    with engine.begin() as c:
        c.execute(text("""UPDATE ocr_document_claims
                             SET lease_expires_at = now() - interval '1 minute'
                           WHERE document_id = ANY(:i)"""), {"i": pool})

    result = _run(2, pool)

    assert result["completed"] == 5, "expired leases must be recoverable by a live worker"
    assert {r["status"] for r in _ocr_rows(pool)} == {"completed"}
    claims = _claims_for(pool)
    assert {c["state"] for c in claims} == {"done"}
    assert all(c["claim_seq"] == 2 for c in claims), "a recovered claim is the second tenure"
    assert all(c["worker_id"] != "crashed-worker" for c in claims)


def test_completed_documents_are_never_reclaimed_by_a_later_run():
    pool = _docs(8)
    first = _run(2, pool)
    assert first["completed"] == 8

    before = {c["document_id"]: c["claim_seq"] for c in _claims_for(pool)}
    second = _run(4, pool)

    assert second["completed"] == 0, "a completed document must not be OCR'd again"
    assert second["claimed"] == 0, "a completed document must not even be claimed again"
    after = {c["document_id"]: c["claim_seq"] for c in _claims_for(pool)}
    assert after == before, "claim tenures must not advance for completed documents"


# --- one slow document must not block the others -----------------------------------------------------

def test_one_slow_document_does_not_block_the_other_workers(monkeypatch):
    monkeypatch.setenv("OCR_TEST_SLOW_SECONDS", "3")
    slow = _docs(1, marker="SLOW ")
    fast = _docs(9, marker="fast ")
    pool = slow + fast

    started = time.monotonic()
    result = _run(2, pool, factory=f"{_DBL}.slow_marked_factory", batch=1)
    elapsed = time.monotonic() - started

    assert result["completed"] == 10
    assert {r["status"] for r in _ocr_rows(pool)} == {"completed"}

    per_worker = sorted(r["completed"] for r in result["per_worker"]
                        if isinstance(r, dict) and "error" not in r)
    assert per_worker[0] < per_worker[-1], (
        "the worker holding the slow document should finish far fewer, proving the other kept going; "
        f"got {per_worker}")
    # Serialised, this would cost the slow document PLUS all nine fast ones on one worker.
    assert elapsed < 20, f"the run took {elapsed:.1f}s — the slow document appears to have blocked"


# --- idempotent writes --------------------------------------------------------------------------------

def test_ocr_writes_stay_idempotent_across_repeated_parallel_runs():
    pool = _docs(10)
    first = _run(2, pool)
    assert first["completed"] == 10

    rows_before = {r["document_id"]: r["text"] for r in _ocr_rows(pool)}

    # Re-running is a no-op: nothing is claimable, so nothing is rewritten.
    again = _run(4, pool)
    assert again["completed"] == 0 and again["claimed"] == 0

    rows_after = {r["document_id"]: r["text"] for r in _ocr_rows(pool)}
    assert rows_after == rows_before, "a repeat run must not rewrite stored text"
    assert len(_ocr_rows(pool)) == 10, "still exactly one OCR row per document"

    # And a forced re-claim of an already-completed document still yields one row, not two.
    with engine.begin() as c:
        c.execute(text("DELETE FROM ocr_document_claims WHERE document_id = ANY(:i)"), {"i": pool})
    third = _run(2, pool)
    assert third["completed"] == 0, "run_ocr must reuse a completed, content-unchanged document"
    assert len(_ocr_rows(pool)) == 10
