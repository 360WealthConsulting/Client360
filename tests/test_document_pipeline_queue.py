"""Continuous document pipeline — the durable queue, discovery, and backpressure.

Unit-level coverage of the mechanisms the rest of the pipeline rests on:

* the claim is atomic and exclusive, and the lease is what survives a crash;
* enqueueing is idempotent on unchanged content and re-queues on changed content;
* retries back off, and exhausted retries become a visible blocker rather than an endless loop;
* discovery resumes from its cursor and does NOT cap the backlog at a page size;
* backpressure refuses to start work without guessing at readings it does not have.

Concurrency, restart-resume and the end-to-end run live in
``tests/test_document_pipeline_service.py``; the stages themselves in
``tests/test_document_pipeline_stages.py``.
"""
import hashlib
import uuid

import pytest
from sqlalchemy import text

from app.db import documents, engine
from app.services.document_pipeline_continuous import backpressure, discovery, model, queue

_DOCS: list[int] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _DOCS:
            ids = tuple(_DOCS)
            for table in ("document_pipeline_blockers", "document_pipeline_ownership_reviews",
                          "document_pipeline_tasks"):
                c.execute(text(f"DELETE FROM {table} WHERE document_id = ANY(:ids)"),
                          {"ids": list(ids)})
            c.execute(documents.delete().where(documents.c.id.in_(ids)))
        c.execute(text("DELETE FROM document_pipeline_workers WHERE worker_id LIKE 'test-%'"))
        # The discovery cursor is shared state in a shared test database; leave it where it started.
        c.execute(text("UPDATE document_pipeline_checkpoints SET cursor_document_id = 0 "
                       "WHERE name = :name"), {"name": model.DISCOVERY_CHECKPOINT})
    _DOCS.clear()


def _doc(*, name="queued.pdf", sha=None, status="active") -> int:
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=name, stored_name=f"dpq-{uuid.uuid4().hex}", storage_path="x",
            size_bytes=10, sha256=sha or hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            status=status, archived=False, tags={}).returning(documents.c.id)).scalar_one()
    _DOCS.append(did)
    return did


def _task(document_id):
    with engine.connect() as c:
        return queue.task_for_document(c, document_id)


def _set_cursor(value: int) -> None:
    with engine.begin() as c:
        c.execute(text("UPDATE document_pipeline_checkpoints SET cursor_document_id = :v "
                       "WHERE name = :name"), {"v": value, "name": model.DISCOVERY_CHECKPOINT})


# --- installation guard ---------------------------------------------------------------------------

def test_tables_are_installed_in_the_test_database():
    assert model.installed(), "the docpipe01 migration must be applied for these tests"
    assert set(model.tables()) == {"tasks", "blockers", "reviews", "workers", "checkpoints"}


# --- enqueue / idempotency ------------------------------------------------------------------------

def test_enqueue_creates_one_task_and_is_idempotent():
    did = _doc()
    with engine.begin() as c:
        assert queue.enqueue(c, did, content_sha256="abc") is True
        assert queue.enqueue(c, did, content_sha256="abc") is False
    task = _task(did)
    assert task["state"] == model.STATE_QUEUED
    assert task["stage"] == model.STAGE_DISCOVERED
    assert task["content_sha256"] == "abc"


def test_enqueue_never_drags_an_in_flight_task_back_to_the_start():
    """A second discovery pass must not reset a document a worker is already partway through."""
    did = _doc()
    with engine.begin() as c:
        queue.enqueue(c, did, content_sha256="abc")
        claimed = queue.claim(c, worker_id="test-a", limit=1)
        queue.advance(c, claimed[0]["id"], worker_id="test-a", next_stage=model.STAGE_CLASSIFY)
        assert queue.enqueue(c, did, content_sha256="abc") is False
    assert _task(did)["stage"] == model.STAGE_CLASSIFY


def test_requeue_only_touches_tasks_at_rest():
    did = _doc()
    with engine.begin() as c:
        queue.enqueue(c, did, content_sha256="old")
        claimed = queue.claim(c, worker_id="test-a", limit=1)
        # Leased: a live worker owns it, so re-queueing must be refused.
        assert queue.requeue(c, did, content_sha256="new") is False
        queue.finish(c, claimed[0]["id"], worker_id="test-a", outcome=model.OUTCOME_LINKED)
        # At rest: now it may be re-queued, from the first stage, with the new hash.
        assert queue.requeue(c, did, content_sha256="new") is True
    task = _task(did)
    assert task["state"] == model.STATE_QUEUED
    assert task["stage"] == model.STAGE_DISCOVERED
    assert task["content_sha256"] == "new"
    assert task["attempts"] == 0
    assert task["outcome"] is None


# --- claim / lease --------------------------------------------------------------------------------

def test_claim_leases_the_task_and_counts_the_attempt():
    did = _doc()
    with engine.begin() as c:
        queue.enqueue(c, did)
        rows = queue.claim(c, worker_id="test-a", limit=5, lease_seconds=120)
    assert [r["document_id"] for r in rows] == [did]
    task = _task(did)
    assert task["state"] == model.STATE_LEASED
    assert task["lease_owner"] == "test-a"
    assert task["attempts"] == 1
    assert task["lease_expires_at"] is not None


def test_a_second_claim_cannot_take_an_already_leased_task():
    did = _doc()
    with engine.begin() as c:
        queue.enqueue(c, did)
        assert len(queue.claim(c, worker_id="test-a", limit=5)) == 1
        assert queue.claim(c, worker_id="test-b", limit=5) == []


def test_claim_skips_a_task_whose_backoff_has_not_elapsed():
    did = _doc()
    with engine.begin() as c:
        queue.enqueue(c, did)
        claimed = queue.claim(c, worker_id="test-a", limit=1)
        queue.retry_later(c, claimed[0]["id"], worker_id="test-a", error="boom", attempts=1)
        assert queue.claim(c, worker_id="test-b", limit=5) == []


def test_only_the_lease_holder_can_advance_or_finish_a_task():
    did = _doc()
    with engine.begin() as c:
        queue.enqueue(c, did)
        claimed = queue.claim(c, worker_id="test-a", limit=1)
        task_id = claimed[0]["id"]
        assert queue.advance(c, task_id, worker_id="test-impostor",
                             next_stage=model.STAGE_OCR) is False
        assert queue.finish(c, task_id, worker_id="test-impostor",
                            outcome=model.OUTCOME_LINKED) is False
    assert _task(did)["stage"] == model.STAGE_DISCOVERED


def test_reclaim_returns_an_expired_lease_to_the_queue():
    did = _doc()
    with engine.begin() as c:
        queue.enqueue(c, did)
        claimed = queue.claim(c, worker_id="test-dead", limit=1)
        queue.advance(c, claimed[0]["id"], worker_id="test-dead", next_stage=model.STAGE_CLASSIFY)
        # Simulate the worker dying: its lease stops being renewed and lapses.
        c.execute(text("UPDATE document_pipeline_tasks SET lease_expires_at = now() - interval '1 hour' "
                       "WHERE id = :id"), {"id": claimed[0]["id"]})
        assert queue.reclaim_expired_leases(c) >= 1
    task = _task(did)
    assert task["state"] == model.STATE_QUEUED
    assert task["lease_owner"] is None
    # The stage it had already reached is PRESERVED — that is what makes a restart resume.
    assert task["stage"] == model.STAGE_CLASSIFY


def test_release_worker_tasks_hands_everything_back_on_a_clean_stop():
    first, second = _doc(), _doc()
    with engine.begin() as c:
        queue.enqueue(c, first)
        queue.enqueue(c, second)
        queue.claim(c, worker_id="test-a", limit=5)
        assert queue.release_worker_tasks(c, worker_id="test-a") == 2
    assert _task(first)["state"] == model.STATE_QUEUED
    assert _task(second)["state"] == model.STATE_QUEUED


# --- retry / backoff / blockers ---------------------------------------------------------------------

@pytest.mark.parametrize(("attempts", "expected"), [(1, 30), (2, 60), (3, 120), (4, 240), (99, 3600)])
def test_retry_backoff_doubles_and_is_capped(attempts, expected):
    assert queue.retry_delay_seconds(attempts) == expected


def test_retry_records_the_error_and_defers_the_task():
    did = _doc()
    with engine.begin() as c:
        queue.enqueue(c, did)
        claimed = queue.claim(c, worker_id="test-a", limit=1)
        assert queue.retry_later(c, claimed[0]["id"], worker_id="test-a",
                                 error="io_error: locked", attempts=2) is True
    task = _task(did)
    assert task["state"] == model.STATE_QUEUED
    assert task["last_error"] == "io_error: locked"
    assert task["last_error_class"] == "transient"
    assert task["lease_owner"] is None


def test_block_moves_the_task_out_of_the_queue_and_opens_one_blocker():
    did = _doc()
    with engine.begin() as c:
        queue.enqueue(c, did)
        claimed = queue.claim(c, worker_id="test-a", limit=1)
        assert queue.block(c, claimed[0]["id"], worker_id="test-a", document_id=did,
                           stage=model.STAGE_OCR, reason_code="encrypted_document",
                           detail="password protected", attempts=1) is True
        rows = queue.open_blockers(c, limit=50)
    task = _task(did)
    assert task["state"] == model.STATE_BLOCKED
    assert task["outcome"] == model.OUTCOME_BLOCKED
    mine = [r for r in rows if r["document_id"] == did]
    assert len(mine) == 1
    assert mine[0]["reason_code"] == "encrypted_document"


def test_a_document_blocked_twice_keeps_exactly_one_blocker_row():
    """Otherwise the blocker count stops being a count of documents and becomes a count of attempts."""
    did = _doc()
    with engine.begin() as c:
        queue.record_blocker(c, document_id=did, stage=model.STAGE_OCR, reason_code="first")
        queue.record_blocker(c, document_id=did, stage=model.STAGE_EXTRACT, reason_code="second")
        rows = [r for r in queue.open_blockers(c, limit=50) if r["document_id"] == did]
    assert len(rows) == 1
    assert rows[0]["reason_code"] == "second"


def test_resolving_a_blocker_does_not_requeue_the_document():
    did = _doc()
    with engine.begin() as c:
        queue.enqueue(c, did)
        claimed = queue.claim(c, worker_id="test-a", limit=1)
        queue.block(c, claimed[0]["id"], worker_id="test-a", document_id=did,
                    stage=model.STAGE_OCR, reason_code="encrypted_document")
        assert queue.resolve_blocker(c, document_id=did, note="checked by hand") is True
        assert [r for r in queue.open_blockers(c, limit=50) if r["document_id"] == did] == []
    assert _task(did)["state"] == model.STATE_BLOCKED


# --- review queue -----------------------------------------------------------------------------------

def test_review_rows_are_one_per_document_and_readable_by_lane():
    did = _doc()
    with engine.begin() as c:
        queue.record_review(c, document_id=did, lane=model.LANE_SHAREPOINT,
                            reason_code="ambiguous", evidence=["two candidates"],
                            candidates=[{"entity_type": "person", "entity_id": 1}])
        queue.record_review(c, document_id=did, lane=model.LANE_SHAREPOINT, reason_code="medium")
        rows = [r for r in queue.open_reviews(c, limit=100) if r["document_id"] == did]
        other_lane = [r for r in queue.open_reviews(c, lane=model.LANE_DRAKE, limit=100)
                      if r["document_id"] == did]
    assert len(rows) == 1
    assert rows[0]["reason_code"] == "medium"
    assert other_lane == []


# --- deduplication ------------------------------------------------------------------------------------

def test_completed_text_is_found_for_an_identical_document_and_never_for_itself():
    sha = hashlib.sha256(b"identical-bytes").hexdigest()
    original, copy = _doc(sha=sha), _doc(sha=sha)
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_ocr (document_id, status, text, char_count, engine) "
                       "VALUES (:id, 'completed', :t, :n, 'client360-ocr')"),
                  {"id": original, "t": "W-2 Wage and Tax Statement", "n": 26})
    with engine.connect() as c:
        found = queue.completed_document_with_text(c, sha, exclude_document_id=copy)
        self_match = queue.completed_document_with_text(c, sha, exclude_document_id=original)
    assert found is not None and found["document_id"] == original
    assert self_match is None, "a document must never deduplicate against itself"


def test_dedupe_ignores_a_completed_row_with_no_text():
    sha = hashlib.sha256(b"empty-ocr").hexdigest()
    original, copy = _doc(sha=sha), _doc(sha=sha)
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_ocr (document_id, status, text, char_count) "
                       "VALUES (:id, 'completed', '', 0)"), {"id": original})
    with engine.connect() as c:
        assert queue.completed_document_with_text(c, sha, exclude_document_id=copy) is None


# --- discovery ------------------------------------------------------------------------------------------

def test_discovery_drains_a_backlog_larger_than_its_page_size():
    """The page size paginates the walk; it must not cap the work. This is the 30/200/500 bug."""
    ids = [_doc(name=f"backlog-{i}.pdf") for i in range(7)]
    _set_cursor(min(ids) - 1)
    result = discovery.discover(page_size=2)      # 7 documents, pages of 2
    assert result["complete"] is True
    assert result["enqueued"] >= 7
    with engine.connect() as c:
        for did in ids:
            assert queue.task_for_document(c, did) is not None


def test_discovery_resumes_from_its_cursor_instead_of_rescanning():
    first = _doc(name="first.pdf")
    _set_cursor(first - 1)
    discovery.discover(page_size=10)
    later = _doc(name="later.pdf")
    second = discovery.discover(page_size=10)
    assert second["enqueued"] == 1, "only the new document should be enqueued on the second pass"
    assert second["cursor"] >= later


def test_discovery_skips_deleted_documents():
    live, dead = _doc(name="live.pdf"), _doc(name="dead.pdf", status="deleted")
    _set_cursor(min(live, dead) - 1)
    discovery.discover(page_size=10)
    with engine.connect() as c:
        assert queue.task_for_document(c, live) is not None
        assert queue.task_for_document(c, dead) is None


def test_discovery_requeues_a_document_whose_content_changed():
    did = _doc(sha="a" * 64)
    _set_cursor(did - 1)
    discovery.discover(page_size=10)
    with engine.begin() as c:
        claimed = queue.claim(c, worker_id="test-a", limit=1)
        queue.finish(c, claimed[0]["id"], worker_id="test-a", outcome=model.OUTCOME_LINKED)
        c.execute(documents.update().where(documents.c.id == did).values(sha256="b" * 64))

    result = discovery.discover(page_size=10)
    assert result["requeued"] == 1
    task = _task(did)
    assert task["state"] == model.STATE_QUEUED
    assert task["content_sha256"] == "b" * 64


def test_discovery_leaves_an_unchanged_document_alone():
    did = _doc(sha="c" * 64)
    _set_cursor(did - 1)
    discovery.discover(page_size=10)
    with engine.begin() as c:
        claimed = queue.claim(c, worker_id="test-a", limit=1)
        queue.finish(c, claimed[0]["id"], worker_id="test-a", outcome=model.OUTCOME_LINKED)
    assert discovery.discover(page_size=10)["requeued"] == 0
    assert _task(did)["state"] == model.STATE_SUCCEEDED


def test_backlog_estimate_separates_undiscovered_from_pending():
    ids = [_doc() for _ in range(3)]
    _set_cursor(min(ids) - 1)
    with engine.connect() as c:
        before = discovery.backlog_estimate(c)
    assert before["undiscovered"] >= 3
    discovery.discover(page_size=10)
    with engine.connect() as c:
        after = discovery.backlog_estimate(c)
    assert after["undiscovered"] == 0
    assert after["pending"] >= 3


# --- backpressure ---------------------------------------------------------------------------------------

def test_database_headroom_gate_refuses_when_the_pool_is_nearly_exhausted():
    class _Pool:
        _max_overflow = 0

        def size(self):
            return 5

        def checkedout(self):
            return 5

    class _Engine:
        pool = _Pool()

    verdict = backpressure.assess(engine_=_Engine(), db_headroom=2)
    assert verdict["ok"] is False
    assert verdict["reason"] == "database_pool_headroom"


def test_missing_psutil_skips_the_cpu_gate_rather_than_inventing_a_reading(monkeypatch):
    """An absent metric must never be reported as 0% — a fabricated reading is worse than none."""
    monkeypatch.setattr(backpressure, "_psutil", None)
    monkeypatch.setattr(backpressure, "_psutil_checked", True)
    reading = backpressure.system_pressure()
    assert reading == {"available": False, "cpu_percent": None, "memory_percent": None}
    verdict = backpressure.assess(db_headroom=0)
    assert verdict["ok"] is True
    assert verdict["system_metrics_available"] is False


def test_cpu_and_memory_gates_fire_when_readings_are_available(monkeypatch):
    monkeypatch.setattr(backpressure, "system_pressure",
                        lambda: {"available": True, "cpu_percent": 97.0, "memory_percent": 10.0})
    assert backpressure.assess(db_headroom=0, cpu_limit_percent=85.0)["reason"] == "cpu_saturated"

    monkeypatch.setattr(backpressure, "system_pressure",
                        lambda: {"available": True, "cpu_percent": 5.0, "memory_percent": 99.0})
    assert backpressure.assess(db_headroom=0, memory_limit_percent=85.0)["reason"] == "memory_saturated"


def test_legacy_sweep_probe_reads_the_advisory_lock_without_taking_it():
    """The pipeline must be able to SEE a sweep's lock without blocking on it.

    A dedicated key, not the real ``_OCR_LOCK_KEY``: PostgreSQL advisory locks are cluster-wide, so a
    genuine OCR sweep running on this server against ANY database would hold the real key and make an
    assertion about it non-deterministic. ``test_the_probe_targets_the_real_sweep_key`` covers the
    wiring to the real key separately."""
    probe_key = 511_005_999

    with engine.connect() as observer:
        assert backpressure.legacy_ocr_sweep_active(observer, lock_key=probe_key) is False
        holder = engine.connect()
        try:
            holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": probe_key})
            assert backpressure.legacy_ocr_sweep_active(observer, lock_key=probe_key) is True
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": probe_key})
            holder.close()
        assert backpressure.legacy_ocr_sweep_active(observer, lock_key=probe_key) is False


def test_the_probe_targets_the_real_sweep_key_by_default(monkeypatch):
    """Without an explicit key the probe must ask about the key ``ocr_runner`` actually takes."""
    from app.jobs.ocr_runner import _OCR_LOCK_KEY

    seen = {}

    class _Conn:
        def execute(self, _statement, params):
            seen.update(params)
            return _Result()

    class _Result:
        def scalar(self):
            return False

    backpressure.legacy_ocr_sweep_active(_Conn())
    assert seen == {"classid": (_OCR_LOCK_KEY >> 32) & 0xFFFFFFFF,
                    "objid": _OCR_LOCK_KEY & 0xFFFFFFFF}
