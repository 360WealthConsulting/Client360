"""Parallel OCR: per-document claiming, crash recovery, idempotency and non-blocking behaviour.

Every test here asserts a property the single global worker lock used to provide for free, and that
per-document claiming must now provide instead. Injected extractors only — no OCR libraries, no real
documents, temp rows tagged and wiped.
"""
import threading
import uuid

import pytest
from sqlalchemy import delete, select, text

from app.db import document_ocr, documents, engine
from app.jobs import ocr_claims, ocr_parallel, ocr_throttle
from app.services import document_ocr as ocr_service

_TAG = "OCRPAR"


@pytest.fixture(autouse=True)
def _health_gate_off(monkeypatch):
    """No Client360 listens during the suite and the health gate fails closed by design, so opt out
    the supported way. Spawned workers inherit os.environ, so this reaches them too. Tests that are
    ABOUT the gate delete this variable themselves."""
    monkeypatch.setenv("OCR_HEALTH_GATE", "0")


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
    _POOL.clear()
    _wipe()
    yield
    _wipe()
    _POOL.clear()


#: The documents the test under way created. Every claim in this module is scoped to it.
#:
#: Claiming is otherwise corpus-wide and ordered by document id, so in a shared database a test that
#: claimed unscoped would take whatever documents other modules had left behind and never reach its
#: own. That is not hypothetical: it is what made this file pass against an empty local database and
#: fail in CI, where the first worker claimed twenty documents and none of them were the test's.
_POOL: list[int] = []


def _docs(n, *, ext="pdf"):
    ids = []
    with engine.begin() as c:
        for i in range(n):
            ids.append(c.execute(documents.insert().values(
                original_name=f"{_TAG} doc {i}.{ext}", stored_name=f"{_TAG}-{uuid.uuid4().hex[:8]}",
                storage_path="/x", storage_provider="Client360 Local", storage_uri="/x",
                size_bytes=10, sha256=uuid.uuid4().hex + uuid.uuid4().hex, status="active",
                archived=False).returning(documents.c.id)).scalar_one())
    _POOL.extend(ids)
    return ids


def _ok(text_out="extracted"):
    return lambda row, path: {"text": text_out, "engine": "fake", "page_count": 1}


def _claim(worker, *, limit=10, mode="initial", lease=900, ids=None):
    with engine.begin() as c:
        return ocr_claims.claim_batch(c, worker_id=worker, mode=mode, limit=limit,
                                      lease_seconds=lease,
                                      document_ids=list(ids) if ids is not None else list(_POOL))


def _expire(document_ids):
    """Simulate a worker that died holding these claims: nothing releases, the lease just lapses."""
    with engine.begin() as c:
        c.execute(text("""UPDATE ocr_document_claims
                             SET lease_expires_at = now() - make_interval(secs => 60)
                           WHERE document_id = ANY(:i)"""), {"i": list(document_ids)})


def _mine(ids, pool):
    return [i for i in ids if i in set(pool)]


# --- no duplicate claims ------------------------------------------------------------------------

def test_two_workers_never_claim_the_same_document():
    pool = _docs(20)
    a = _mine([c.document_id for c in _claim("worker-a", limit=20)], pool)
    b = _mine([c.document_id for c in _claim("worker-b", limit=20)], pool)
    assert len(a) == 20, "first worker should take everything available"
    assert b == [], "second worker must get nothing: every lease is live"
    assert not set(a) & set(b)


def test_claim_batch_never_claims_more_than_it_returns():
    """Regression: an oversampled candidate window claimed rows the caller never received, so those
    documents sat locked behind a live lease with nobody working on them until it lapsed."""
    pool = _docs(20)
    returned = _mine([c.document_id for c in _claim("w", limit=5)], pool)
    assert len(returned) == 5
    with engine.connect() as c:
        locked = c.execute(text("""
            SELECT count(*) FROM ocr_document_claims cl JOIN documents d ON d.id = cl.document_id
             WHERE d.original_name LIKE :tag AND cl.state = 'claimed'"""),
            {"tag": f"%{_TAG}%"}).scalar()
    assert locked == 5, f"claimed {locked} rows but handed back 5"


def test_claiming_can_be_scoped_to_an_explicit_document_set():
    """Regression: claiming is corpus-wide and ordered by id, so an unscoped claim in a populated
    database takes other documents entirely. A targeted run over a manifest must claim only its own
    documents, and an empty scope must claim nothing rather than everything."""
    pool = _docs(10)
    half = pool[:4]
    got = [c.document_id for c in _claim("scoped", limit=10, ids=half)]
    assert sorted(got) == sorted(half), "a scoped claim must stay inside its set"

    with engine.begin() as c:
        assert ocr_claims.claim_batch(c, worker_id="empty", mode="initial", limit=10,
                                      document_ids=[]) == []


def test_concurrent_claiming_produces_no_overlap():
    pool = _docs(40)
    seen, errors, lock = [], [], threading.Lock()

    def grab(name):
        try:
            got = _mine([c.document_id for c in _claim(name, limit=40)], pool)
            with lock:
                seen.extend(got)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=grab, args=(f"w{i}",)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"claiming raised under concurrency: {errors}"
    assert len(seen) == len(set(seen)), "a document was claimed by more than one worker"
    assert set(seen) <= set(pool)
    with engine.connect() as c:
        assert ocr_claims.duplicate_live_claims(c) == []


# --- crash recovery -----------------------------------------------------------------------------

def test_expired_lease_is_reclaimed_by_another_worker():
    pool = _docs(5)
    first = _mine([c.document_id for c in _claim("dead-worker", limit=5)], pool)
    assert len(first) == 5
    assert _mine([c.document_id for c in _claim("live-worker", limit=5)], pool) == []

    _expire(first)                                    # the holder died without releasing

    recovered = _mine([c.document_id for c in _claim("live-worker", limit=5)], pool)
    assert sorted(recovered) == sorted(first), "expired leases must be recoverable"


def test_reclaim_bumps_seq_so_the_dead_worker_cannot_write():
    _docs(1)
    stale = _claim("dead-worker", limit=1)[0]
    _expire([stale.document_id])
    fresh = _claim("live-worker", limit=1)[0]

    assert fresh.document_id == stale.document_id
    assert fresh.seq == stale.seq + 1
    with engine.connect() as c:
        assert not ocr_claims.claim_is_current(c, worker_id="dead-worker", claim=stale)
        assert ocr_claims.claim_is_current(c, worker_id="live-worker", claim=fresh)


def test_a_lost_claim_cannot_complete_over_the_new_holder():
    _docs(1)
    stale = _claim("dead-worker", limit=1)[0]
    _expire([stale.document_id])
    fresh = _claim("live-worker", limit=1)[0]

    with engine.begin() as c:
        assert ocr_claims.complete(c, worker_id="dead-worker", claim=stale, outcome="late") is False
        assert ocr_claims.complete(c, worker_id="live-worker", claim=fresh, outcome="ok") is True


def test_stale_claims_are_visible_to_operators():
    pool = _docs(3)
    held = _mine([c.document_id for c in _claim("dead-worker", limit=3)], pool)
    _expire(held)
    with engine.connect() as c:
        stale_ids = {r["document_id"] for r in ocr_claims.stale_claims(c)}
    assert set(held) <= stale_ids


# --- completed documents are never reprocessed ---------------------------------------------------

def test_completed_documents_are_not_claimable():
    pool = _docs(6)
    ocr_service.run_ocr(document_ids=pool[:3], extractor=_ok(), isolate=False, mode="initial")
    with engine.connect() as c:
        done = list(c.scalars(select(document_ocr.c.document_id).where(
            document_ocr.c.document_id.in_(pool), document_ocr.c.status == "completed")))
    assert len(done) == 3

    claimed = _mine([c.document_id for c in _claim("w", limit=10)], pool)
    assert not set(claimed) & set(done), "a completed document was offered for claiming"
    assert sorted(claimed) == sorted(pool[3:])


def test_a_done_claim_is_never_handed_out_again():
    pool = _docs(2)
    claims = _claim("w1", limit=2)
    with engine.begin() as c:
        for cl in claims:
            ocr_claims.complete(c, worker_id="w1", claim=cl, outcome="completed")
    _expire([c.document_id for c in claims])          # even with a lapsed lease
    assert _mine([c.document_id for c in _claim("w2", limit=2)], pool) == []


# --- restart from the existing checkpoint --------------------------------------------------------

def test_restart_resumes_and_does_not_redo_finished_work():
    pool = _docs(10)
    first = _claim("run-1", limit=4)
    ocr_service.run_ocr(document_ids=[c.document_id for c in first], extractor=_ok(),
                        isolate=False, mode="initial")
    with engine.begin() as c:
        for cl in first:
            ocr_claims.complete(c, worker_id="run-1", claim=cl, outcome="completed")

    # "Restart": a brand-new worker identity, nothing carried over in memory.
    resumed = _mine([c.document_id for c in _claim("run-2", limit=20)], pool)
    assert sorted(resumed) == sorted(pool[4:]), "restart must resume from the database, not repeat"


# --- idempotent writes ---------------------------------------------------------------------------

def test_repeated_run_ocr_on_the_same_document_is_idempotent():
    pool = _docs(3)
    first = ocr_service.run_ocr(document_ids=pool, extractor=_ok("once"), isolate=False,
                                mode="initial")
    second = ocr_service.run_ocr(document_ids=pool, extractor=_ok("twice"), isolate=False,
                                 mode="initial")
    assert first["completed"] == 3
    assert second["completed"] == 0 and second["skipped"] == 3, "second pass must reuse, not rewrite"

    with engine.connect() as c:
        rows = list(c.execute(select(document_ocr.c.document_id, document_ocr.c.text)
                              .where(document_ocr.c.document_id.in_(pool))))
    assert len(rows) == 3, "one OCR row per document, never a duplicate"
    assert {r[1] for r in rows} == {"once"}, "the reused path must not overwrite stored text"


def test_two_workers_racing_the_same_document_write_once():
    pool = _docs(1)
    barrier = threading.Barrier(2)
    results, lock = [], threading.Lock()

    def race(name):
        barrier.wait()
        got = _claim(name, limit=1)
        if got:
            ocr_service.run_ocr(document_ids=[c.document_id for c in got], extractor=_ok(name),
                                isolate=False, mode="initial")
        with lock:
            results.append((name, [c.document_id for c in got]))

    threads = [threading.Thread(target=race, args=(n,)) for n in ("racer-a", "racer-b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [n for n, got in results if got]
    assert len(winners) == 1, f"exactly one worker may win the document, got {winners}"
    with engine.connect() as c:
        rows = list(c.execute(select(document_ocr.c.document_id)
                              .where(document_ocr.c.document_id.in_(pool))))
    assert len(rows) == 1


# --- one slow document must not block other workers ----------------------------------------------

def test_a_slow_document_does_not_block_other_workers():
    pool = _docs(12)
    slow_started, release_slow = threading.Event(), threading.Event()
    fast_done = []

    def slow_extractor(row, path):
        slow_started.set()
        release_slow.wait(timeout=10)
        return {"text": "slow", "engine": "fake", "page_count": 1}

    def slow_worker():
        got = _claim("slow-worker", limit=1)
        ocr_service.run_ocr(document_ids=[c.document_id for c in got],
                            extractor=slow_extractor, isolate=False, mode="initial")

    t = threading.Thread(target=slow_worker, daemon=True)
    t.start()
    assert slow_started.wait(timeout=10), "slow document never started"

    # While exactly one document is wedged, another worker must still make full progress.
    got = _mine([c.document_id for c in _claim("fast-worker", limit=11)], pool)
    summary = ocr_service.run_ocr(document_ids=got, extractor=_ok(), isolate=False, mode="initial")
    fast_done.append(summary["completed"])

    release_slow.set()
    t.join(timeout=15)
    assert fast_done[0] == 11, "the other worker must finish every remaining document"


# --- reconciliation ------------------------------------------------------------------------------

def test_global_totals_reconcile():
    _docs(8)
    claims = _claim("w", limit=8)
    ocr_service.run_ocr(document_ids=[c.document_id for c in claims], extractor=_ok(),
                        isolate=False, mode="initial")
    with engine.begin() as c:
        for cl in claims:
            ocr_claims.complete(c, worker_id="w", claim=cl, outcome="completed")

    with engine.connect() as c:
        totals = ocr_claims.reconcile(c)
        mine_done = c.execute(text("""
            SELECT count(*) FROM ocr_document_claims cl JOIN documents d ON d.id = cl.document_id
             WHERE d.original_name LIKE :tag AND cl.state = 'done'"""),
            {"tag": f"%{_TAG}%"}).scalar()
        mine_completed = c.execute(text("""
            SELECT count(*) FROM document_ocr o JOIN documents d ON d.id = o.document_id
             WHERE d.original_name LIKE :tag AND o.status = 'completed'"""),
            {"tag": f"%{_TAG}%"}).scalar()

    assert mine_done == 8
    assert mine_completed == 8, "every done claim must correspond to a completed OCR row"
    assert totals["stale"] >= 0 and totals["claims_total"] >= 8
    assert ocr_claims.duplicate_live_claims(engine.connect()) == []


# --- admission control ---------------------------------------------------------------------------

def test_health_gate_blocks_new_claims_when_configured_and_failing(monkeypatch):
    monkeypatch.delenv("OCR_HEALTH_GATE", raising=False)      # this test is ABOUT the gate
    bad = ocr_throttle.may_claim(health_url="http://127.0.0.1:9/health", min_free_mb=0,
                                 max_cpu_percent=100.0)
    assert not bad
    assert "health" in bad.reason.lower()


def test_an_unconfigured_health_gate_now_FAILS_CLOSED(monkeypatch):
    """Contract change: an unset variable used to mean "no opinion" and waved work through, so the
    gate protected nothing on a box where nobody had wired a URL. It now defaults to the local
    /health and /readiness pair and holds when it cannot confirm both."""
    for var in ("OCR_HEALTH_GATE", "CLIENT360_HEALTH_URLS", "CLIENT360_HEALTH_URL"):
        monkeypatch.delenv(var, raising=False)
    assert ocr_throttle.configured_health_urls() == ocr_throttle.DEFAULT_HEALTH_URLS
    monkeypatch.setattr(ocr_throttle, "_probe", lambda url, t: (False, f"{url} unreachable"))
    ok, _ = ocr_throttle.health_ok()
    assert not ok, "with nothing configured the gate must protect, not wave work through"


def test_an_explicit_empty_override_is_an_opt_out(monkeypatch):
    monkeypatch.delenv("OCR_HEALTH_GATE", raising=False)
    ok, detail = ocr_throttle.health_ok("")
    assert ok and "disabled" in detail


def test_memory_floor_blocks_new_claims():
    blocked = ocr_throttle.may_claim(min_free_mb=1 << 30, max_cpu_percent=100.0, health_url="")
    assert not blocked
    assert "memory" in blocked.reason.lower()


def test_cpu_ceiling_blocks_new_claims():
    blocked = ocr_throttle.may_claim(min_free_mb=0, max_cpu_percent=-1.0, health_url="",
                                     sample_seconds=0.05)
    assert not blocked
    assert "cpu" in blocked.reason.lower()


def test_worker_count_is_configurable(monkeypatch):
    monkeypatch.setenv("OCR_PARALLEL_WORKERS", "3")
    assert ocr_throttle.configured_workers() == 3
    monkeypatch.setenv("OCR_PARALLEL_WORKERS", "nonsense")
    assert ocr_throttle.configured_workers() == ocr_throttle.DEFAULT_WORKERS


# --- the worker loop end to end -------------------------------------------------------------------

def test_worker_loop_drains_its_lane_and_stops():
    pool = _docs(7)
    totals = ocr_parallel.worker_loop(worker_id="loop-1", mode="initial", batch=3,
                                      extractor=_ok(), factory_ref=None, stop_when_empty=True,
                                      document_ids=pool)
    with engine.connect() as c:
        done = c.execute(text("""
            SELECT count(*) FROM document_ocr o JOIN documents d ON d.id = o.document_id
             WHERE d.original_name LIKE :tag AND o.status = 'completed'"""),
            {"tag": f"%{_TAG}%"}).scalar()
    assert done == 7
    assert totals["claimed"] >= 7
    assert totals["lost_claims"] == 0
