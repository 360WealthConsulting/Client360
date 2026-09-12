"""Continuous document pipeline — end to end, under concurrency, across a restart, and when it fails.

The four properties that make this a pipeline rather than a script, each tested as a property rather
than as a sequence of calls:

* END TO END — a document appears, and without anyone running anything it ends up classified and
  linked to the right client.
* CONCURRENCY — several workers over one queue never process the same document twice.
* RESTART-RESUME — a worker killed mid-document does not restart that document from the beginning,
  and its work does not become invisible.
* FAILURE-RETRY — a transient failure backs off and is retried; an exhausted or permanent one becomes
  a visible blocker instead of an endless loop.

Plus the operations surface: metrics, stall detection, and the three read-only routes.
"""
import hashlib
import threading
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from app.db import documents, engine, people, person_source_links, source_contacts
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.document_pipeline_continuous import metrics, model, queue, stages, worker
from app.services.document_pipeline_continuous import service as pipeline
from app.services.document_pipeline_continuous.model import PipelineTransientError

_TAG = uuid.uuid4().hex[:8].translate(str.maketrans("0123456789", "uvwxyzabcd")).capitalize()
_DOCS: list[int] = []
_PEOPLE: list[int] = []
_SOURCE_CONTACTS: list[int] = []
_LINKS: list[int] = []

VIEWER = Principal(1, "ops@example.com", "Ops", frozenset({"documents.view", "record.read_all"}))
OUTSIDER = Principal(2, "no@example.com", "No", frozenset({"record.read_all"}))


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _DOCS:
            ids = list(_DOCS)
            for table in ("document_pipeline_blockers", "document_pipeline_ownership_reviews",
                          "document_pipeline_tasks", "document_sources", "document_ocr",
                          "document_facts", "document_classifications"):
                c.execute(text(f"DELETE FROM {table} WHERE document_id = ANY(:ids)"), {"ids": ids})
            c.execute(documents.delete().where(documents.c.id.in_(ids)))
        if _LINKS:
            c.execute(person_source_links.delete().where(person_source_links.c.id.in_(_LINKS)))
        if _SOURCE_CONTACTS:
            c.execute(source_contacts.delete().where(source_contacts.c.id.in_(_SOURCE_CONTACTS)))
        if _PEOPLE:
            c.execute(people.delete().where(people.c.id.in_(_PEOPLE)))
        c.execute(text("DELETE FROM document_pipeline_workers WHERE worker_id LIKE '%test%'"))
        c.execute(text("UPDATE document_pipeline_checkpoints SET cursor_document_id = 0 "
                       "WHERE name = :n"), {"n": model.DISCOVERY_CHECKPOINT})
    for bucket in (_DOCS, _PEOPLE, _SOURCE_CONTACTS, _LINKS):
        bucket.clear()


def _person(full_name: str) -> int:
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, active=True,
                                               contact_type="Client")
                        .returning(people.c.id)).scalar_one()
    _PEOPLE.append(pid)
    return pid


def _known_email(person_id: int, email: str) -> None:
    """Give a person a confirmed source-contact email, which is the strongest content signal there is."""
    with engine.begin() as c:
        sid = c.execute(source_contacts.insert().values(
            source_system="TaxDome", source_file="t.zip", source_record_id=uuid.uuid4().hex,
            source_hash=uuid.uuid4().hex, email=email, raw_data={})
            .returning(source_contacts.c.id)).scalar_one()
        lid = c.execute(person_source_links.insert().values(
            person_id=person_id, source_contact_id=sid, match_method="email", confirmed=True)
            .returning(person_source_links.c.id)).scalar_one()
    _SOURCE_CONTACTS.append(sid)
    _LINKS.append(lid)


def _doc(*, name="f.txt", path=None, sha=None, source_system="SharePoint") -> int:
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=name, stored_name=f"dpi-{uuid.uuid4().hex}",
            storage_path=str(path) if path else "x", storage_uri=str(path) if path else None,
            size_bytes=10, sha256=sha or hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            status="active", archived=False, tags={}).returning(documents.c.id)).scalar_one()
        if source_system:
            c.execute(text("INSERT INTO document_sources (document_id, source_system, source_uri) "
                           "VALUES (:d, :s, '')"), {"d": did, "s": source_system})
    _DOCS.append(did)
    return did


def _set_cursor_below(document_ids) -> None:
    with engine.begin() as c:
        c.execute(text("UPDATE document_pipeline_checkpoints SET cursor_document_id = :v "
                       "WHERE name = :n"),
                  {"v": min(document_ids) - 1, "n": model.DISCOVERY_CHECKPOINT})


def _task(document_id):
    with engine.connect() as c:
        return queue.task_for_document(c, document_id)


def _owner(document_id):
    with engine.connect() as c:
        return tuple(c.execute(select(documents.c.person_id, documents.c.household_id,
                                      documents.c.organization_id)
                               .where(documents.c.id == document_id)).first())


def _stub_stages(monkeypatch, *, record=None, fail_with=None):
    """Replace the stage executors with a fast walk through the same stage graph.

    The stages have their own suite; what these tests are about is the QUEUE behaviour around them,
    and driving real extraction/OCR here would make a concurrency test a test of the OCR engine.

    The OCR stage is stubbed at its three phases rather than at ``run_stage``, because the worker
    drives those phases directly — so the worker's real OCR sequencing (plan, execute without a
    transaction, settle) is still the code under test. Without this the stage would reach the live
    deferral check and park itself behind whatever OCR sweep happens to be running on the host."""
    lock = threading.Lock()

    def _note(document_id, stage):
        if record is not None:
            with lock:
                record.append((document_id, stage))

    def fake_run_stage(_conn, task, **_kwargs):
        _note(task["document_id"], task["stage"])
        if fail_with is not None:
            raise fail_with
        nxt = model.NEXT_STAGE[task["stage"]]
        outcome = model.OUTCOME_LINKED if nxt == model.STAGE_DONE else None
        return stages.StageResult(nxt, outcome=outcome)

    def fake_plan_ocr(_conn, task, **_kwargs):
        _note(task["document_id"], model.STAGE_OCR)
        if fail_with is not None:
            raise fail_with
        return stages.OcrPlan("run", int(task["document_id"]))

    monkeypatch.setattr(worker.stages, "run_stage", fake_run_stage)
    monkeypatch.setattr(worker.stages, "plan_ocr", fake_plan_ocr)
    monkeypatch.setattr(worker.stages, "execute_ocr",
                        lambda _plan, **_kwargs: {"status": "completed", "chars_extracted": 10})
    monkeypatch.setattr(worker.stages, "settle_ocr",
                        lambda _conn, _plan, _summary: stages.StageResult(model.STAGE_CLASSIFY))
    return record


# --- end to end -----------------------------------------------------------------------------------

def test_a_new_document_is_discovered_classified_and_linked_without_anyone_running_anything(tmp_path):
    """The whole point: a file lands, and the pipeline alone turns it into an owned, classified record."""
    email = f"pipe-{_TAG.lower()}@mail.com"
    pid = _person(f"Zephyrina {_TAG}")
    _known_email(pid, email)
    f = tmp_path / "Form1095a_2021.txt"
    f.write_text("Form 1095-A Health Insurance Marketplace Statement\n"
                 f"Dear Zephyrina {_TAG},\ncontact {email}\n")
    did = _doc(name="Form1095a_2021.txt", path=f)
    _set_cursor_below([did])

    found = pipeline.discover()
    assert found["enqueued"] >= 1
    pipeline.drain(max_passes=20)

    task = _task(did)
    assert task["state"] == model.STATE_SUCCEEDED
    assert task["stage"] == model.STAGE_DONE
    assert task["outcome"] == model.OUTCOME_LINKED
    assert _owner(did) == (pid, None, None)

    with engine.connect() as c:
        doc_type = c.execute(text("SELECT doc_type FROM document_classifications "
                                  "WHERE document_id = :d"), {"d": did}).scalar()
        ocr_status = c.execute(text("SELECT status, engine FROM document_ocr WHERE document_id = :d"),
                               {"d": did}).mappings().first()
    assert doc_type == "1095-A"
    assert ocr_status["status"] == "completed"
    assert ocr_status["engine"] == "embedded:plaintext", "a text file must never reach the OCR engine"


def test_a_second_run_over_unchanged_documents_does_no_work(tmp_path):
    """Idempotency: the pipeline must be safe to leave running forever."""
    f = tmp_path / "note.txt"
    f.write_text("An ordinary letter with no identifying content whatsoever in it at all.\n")
    did = _doc(name="note.txt", path=f)
    _set_cursor_below([did])
    pipeline.discover()
    pipeline.drain(max_passes=20)
    first = _task(did)

    pipeline.discover()
    second_pass = pipeline.drain(max_passes=20)
    second = _task(did)

    assert second_pass["claimed"] == 0
    assert second["attempts"] == first["attempts"]
    assert second["completed_at"] == first["completed_at"]


def test_draining_is_not_capped_at_a_document_count(monkeypatch):
    """The processing half of the no-arbitrary-limit rule.

    With one document per claim, a default pass budget would BE a document limit — the same silent
    truncation the sweeps have. Twenty-five documents is more than any plausible accidental cap in a
    loop that claims one at a time."""
    _stub_stages(monkeypatch)
    ids = [_doc(name=f"u{i}.txt") for i in range(25)]
    with engine.begin() as c:
        for did in ids:
            queue.enqueue(c, did)

    result = pipeline.drain(batch_size=1)      # no max_passes: drain until empty

    assert result["completed"] == 25
    assert all(_task(d)["state"] == model.STATE_SUCCEEDED for d in ids)


def test_the_drain_pass_budget_is_opt_in():
    """A default budget would look like a safety net and behave like a work limit."""
    import inspect

    assert inspect.signature(worker.drain).parameters["max_passes"].default is None
    assert inspect.signature(pipeline.drain).parameters["max_passes"].default is None


def test_an_explicit_pass_budget_stops_early_and_says_so(monkeypatch):
    """A caller with a time budget gets one, and is told the queue is not empty."""
    _stub_stages(monkeypatch)
    ids = [_doc(name=f"b{i}.txt") for i in range(5)]
    with engine.begin() as c:
        for did in ids:
            queue.enqueue(c, did)

    result = pipeline.drain(max_passes=2, batch_size=1)

    assert result["exhausted_budget"] is True
    assert result["completed"] == 2
    assert any(_task(d)["state"] == model.STATE_QUEUED for d in ids)


def test_the_tick_composes_discovery_and_processing(monkeypatch, tmp_path):
    f = tmp_path / "tick.txt"
    f.write_text("Another perfectly ordinary document with some text inside of it.\n")
    did = _doc(name="tick.txt", path=f)
    _set_cursor_below([did])
    _stub_stages(monkeypatch)
    result = pipeline.tick()
    assert result["discovery"]["enqueued"] >= 1
    assert result["processing"]["completed"] >= 1
    assert _task(did)["state"] == model.STATE_SUCCEEDED


# --- concurrency ------------------------------------------------------------------------------------

def test_concurrent_claims_never_hand_the_same_document_to_two_workers():
    """The core mutual-exclusion guarantee, exercised against real concurrent transactions."""
    ids = [_doc(name=f"c{i}.txt") for i in range(16)]
    with engine.begin() as c:
        for did in ids:
            queue.enqueue(c, did)

    claimed: list[int] = []
    lock = threading.Lock()
    start = threading.Barrier(4)

    def _claimer(index):
        start.wait(timeout=10)
        mine = []
        while True:
            with engine.begin() as conn:
                rows = queue.claim(conn, worker_id=f"test-race-{index}", limit=3)
            if not rows:
                break
            mine.extend(int(r["document_id"]) for r in rows)
        with lock:
            claimed.extend(mine)

    threads = [threading.Thread(target=_claimer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert sorted(claimed) == sorted(ids), "every document must be claimed exactly once"
    assert len(claimed) == len(set(claimed)), "a document was handed to two workers"


def test_parallel_workers_process_each_document_exactly_once(monkeypatch):
    seen: list[tuple[int, str]] = []
    _stub_stages(monkeypatch, record=seen)
    ids = [_doc(name=f"p{i}.txt") for i in range(12)]
    with engine.begin() as c:
        for did in ids:
            queue.enqueue(c, did)

    stop = threading.Event()
    results = worker.run_pool(workers=3, stop_event=stop, max_passes=8, batch_size=2,
                              idle_sleep=0.01, pressure_limits={"db_headroom": 0})

    ownership_stage_runs = [d for d, stage in seen if stage == model.STAGE_OWNERSHIP]
    assert sorted(ownership_stage_runs) == sorted(ids)
    assert len(ownership_stage_runs) == len(set(ownership_stage_runs))
    assert sum(r["completed"] for r in results) == len(ids)


def test_a_worker_that_lost_its_lease_writes_nothing():
    """A reclaimed worker coming back from a long stage must not stamp its verdict on the document."""
    did = _doc()
    with engine.begin() as c:
        queue.enqueue(c, did)
        claimed = queue.claim(c, worker_id="test-slow", limit=1)
        task_id = claimed[0]["id"]
        # Another worker reclaims it while 'test-slow' is busy.
        c.execute(text("UPDATE document_pipeline_tasks SET lease_expires_at = now() - interval '1h' "
                       "WHERE id = :id"), {"id": task_id})
        queue.reclaim_expired_leases(c)
        queue.claim(c, worker_id="test-fast", limit=1)
        # The original worker finally finishes and tries to record its result.
        assert queue.finish(c, task_id, worker_id="test-slow",
                            outcome=model.OUTCOME_LINKED) is False
    assert _task(did)["lease_owner"] == "test-fast"


# --- restart and resume ------------------------------------------------------------------------------

def test_a_restart_resumes_at_the_stage_the_document_reached(monkeypatch):
    """A killed worker must not make the pipeline re-do the expensive stages it already finished."""
    seen: list[tuple[int, str]] = []
    _stub_stages(monkeypatch, record=seen)
    did = _doc()
    with engine.begin() as c:
        queue.enqueue(c, did)

    crashed = worker.Worker(worker_id="test-crashed", pressure_limits={"db_headroom": 0})
    crashed.register()
    with engine.begin() as c:
        task = queue.claim(c, worker_id="test-crashed", limit=1)[0]
    task = dict(task)
    task["stage"] = stages.first_stage_of(task)
    crashed._run_one_stage(task)      # completes 'extract', then the process "dies"

    assert _task(did)["stage"] == model.STAGE_OCR
    with engine.begin() as c:
        c.execute(text("UPDATE document_pipeline_tasks SET lease_expires_at = now() - interval '1h' "
                       "WHERE document_id = :d"), {"d": did})

    worker.Worker(worker_id="test-restarted", pressure_limits={"db_headroom": 0},
                  idle_sleep=0.01).run_forever(max_passes=6)

    stages_run = [stage for _d, stage in seen]
    assert stages_run.count(model.STAGE_EXTRACT) == 1, "extraction must not be repeated after a restart"
    assert _task(did)["state"] == model.STATE_SUCCEEDED


def test_a_clean_shutdown_hands_work_back_immediately(monkeypatch):
    """A planned restart must not leave documents invisible until their leases lapse."""
    _stub_stages(monkeypatch)
    did = _doc()
    stopping = worker.Worker(worker_id="test-stopping")
    stopping.register()
    with engine.begin() as c:
        queue.enqueue(c, did)
        queue.claim(c, worker_id="test-stopping", limit=1)

    stopping.shutdown()

    task = _task(did)
    assert task["state"] == model.STATE_QUEUED
    assert task["lease_owner"] is None
    with engine.connect() as c:
        state = c.execute(text("SELECT state FROM document_pipeline_workers WHERE worker_id = :w"),
                          {"w": "test-stopping"}).scalar()
    assert state == "stopped"


def test_unfinished_work_survives_a_process_restart(monkeypatch):
    """Nothing is held in memory: a brand-new process finds the same queue and finishes the job."""
    seen: list[tuple[int, str]] = []
    _stub_stages(monkeypatch, record=seen)
    ids = [_doc(name=f"r{i}.txt") for i in range(4)]
    _set_cursor_below(ids)
    pipeline.discover()

    first = worker.Worker(worker_id="test-before", pressure_limits={"db_headroom": 0},
                          batch_size=1)
    first.register()
    first.run_once()                 # one document only, then "the service is stopped"
    first.shutdown()

    remaining = [d for d in ids if _task(d)["state"] != model.STATE_SUCCEEDED]
    assert remaining, "the first pass should not have finished everything"

    pipeline.drain(max_passes=20)    # a fresh worker in what is, as far as it knows, a new process
    assert all(_task(d)["state"] == model.STATE_SUCCEEDED for d in ids)


# --- failure and retry ---------------------------------------------------------------------------------

def test_a_transient_failure_is_retried_with_backoff(monkeypatch):
    _stub_stages(monkeypatch, fail_with=PipelineTransientError("the file was locked"))
    did = _doc()
    with engine.begin() as c:
        queue.enqueue(c, did)

    worker.Worker(worker_id="test-retry", pressure_limits={"db_headroom": 0}).run_once()

    task = _task(did)
    assert task["state"] == model.STATE_QUEUED
    assert task["attempts"] == 1
    assert task["last_error_class"] == "transient"
    assert task["available_at"] > task["updated_at"], "the retry must be deferred, not immediate"


def test_exhausted_retries_become_a_visible_blocker(monkeypatch):
    """The failure has to stop being a retry and start being something a person can see."""
    _stub_stages(monkeypatch, fail_with=PipelineTransientError("still locked"))
    did = _doc()
    with engine.begin() as c:
        queue.enqueue(c, did, max_attempts=2)

    retrying = worker.Worker(worker_id="test-exhaust", pressure_limits={"db_headroom": 0})
    for _ in range(2):
        retrying.run_once()
        with engine.begin() as c:      # skip the backoff wait
            c.execute(text("UPDATE document_pipeline_tasks SET available_at = now() "
                           "WHERE document_id = :d"), {"d": did})

    task = _task(did)
    assert task["state"] == model.STATE_BLOCKED
    assert task["outcome"] == model.OUTCOME_BLOCKED
    with engine.connect() as c:
        blockers = [b for b in queue.open_blockers(c, limit=200) if b["document_id"] == did]
    assert blockers and blockers[0]["reason_code"] == "attempts_exhausted"


def test_a_permanent_failure_skips_the_retries_entirely(monkeypatch):
    from app.services.document_pipeline_continuous.model import PipelinePermanentError

    _stub_stages(monkeypatch, fail_with=PipelinePermanentError("encrypted_document", "locked"))
    did = _doc()
    with engine.begin() as c:
        queue.enqueue(c, did, max_attempts=5)

    worker.Worker(worker_id="test-permanent", pressure_limits={"db_headroom": 0}).run_once()

    task = _task(did)
    assert task["state"] == model.STATE_BLOCKED
    assert task["attempts"] == 1, "a permanent failure must not burn four more attempts first"
    with engine.connect() as c:
        blockers = [b for b in queue.open_blockers(c, limit=200) if b["document_id"] == did]
    assert blockers[0]["reason_code"] == "encrypted_document"


def test_requeue_clears_the_blocker_and_puts_the_document_back(monkeypatch):
    from app.services.document_pipeline_continuous.model import PipelinePermanentError

    _stub_stages(monkeypatch, fail_with=PipelinePermanentError("source_file_missing", "gone"))
    did = _doc()
    with engine.begin() as c:
        queue.enqueue(c, did)
    worker.Worker(worker_id="test-fixme", pressure_limits={"db_headroom": 0}).run_once()
    assert _task(did)["state"] == model.STATE_BLOCKED

    assert pipeline.requeue_document(did) is True
    assert _task(did)["state"] == model.STATE_QUEUED
    assert _task(did)["stage"] == model.STAGE_DISCOVERED
    with engine.connect() as c:
        assert [b for b in queue.open_blockers(c, limit=200) if b["document_id"] == did] == []


def test_backpressure_pauses_claiming_instead_of_failing(monkeypatch):
    """Refusing to start work under load is the pipeline working, not the pipeline breaking."""
    monkeypatch.setattr(worker.backpressure, "assess",
                        lambda **_kwargs: {"ok": False, "reason": "cpu_saturated"})
    did = _doc()
    with engine.begin() as c:
        queue.enqueue(c, did)
    result = worker.Worker(worker_id="test-pressure").run_once()
    assert result["claimed"] == 0
    assert result["deferred"] == "cpu_saturated"
    assert _task(did)["state"] == model.STATE_QUEUED


# --- metrics and health -----------------------------------------------------------------------------------

def test_the_snapshot_reports_the_nine_headline_numbers():
    snapshot = pipeline.status()
    assert snapshot["installed"] is True
    for key in ("backlog", "running", "completed", "linked", "review", "blocked", "failed",
                "throughput", "last_heartbeat_at"):
        assert key in snapshot, f"the operations snapshot is missing {key}"
    assert {"last_1m", "last_5m", "last_60m"} <= set(snapshot["throughput"])


def test_the_snapshot_counts_this_pipeline_run(monkeypatch):
    _stub_stages(monkeypatch)
    before = pipeline.status()
    ids = [_doc(name=f"m{i}.txt") for i in range(3)]
    _set_cursor_below(ids)
    pipeline.discover()
    pipeline.drain(max_passes=20)
    after = pipeline.status()
    assert after["completed"] - before["completed"] >= 3
    assert after["linked"] - before["linked"] >= 3
    assert after["throughput"]["last_60m"] >= 3


def _snapshot(**overrides):
    base = {"installed": True, "backlog": 0, "running": 0,
            "throughput": {"last_1m": 0, "last_5m": 0, "last_60m": 0},
            "workers": {"live": 0, "last_heartbeat_age_seconds": None}}
    base.update(overrides)
    return base


def test_an_empty_queue_is_idle_and_healthy_not_a_fault():
    """A finished backlog reported as an incident is how monitoring gets muted."""
    status = metrics.health(snapshot_=_snapshot())
    assert status["status"] == "idle"
    assert status["healthy"] is True


def test_a_backlog_with_no_live_worker_is_reported_as_stopped():
    status = metrics.health(snapshot_=_snapshot(backlog=42))
    assert status["status"] == "stopped"
    assert status["healthy"] is False
    assert "42" in status["reason"]


def test_a_worker_that_stopped_progressing_is_a_stall():
    status = metrics.health(
        snapshot_=_snapshot(backlog=5, running=1,
                            workers={"live": 1, "last_heartbeat_age_seconds": 4000}),
        stall_seconds=900)
    assert status["status"] == "stalled"
    assert status["healthy"] is False


def test_a_worker_grinding_through_one_big_document_is_not_a_stall():
    """The heartbeat advances DURING a document, so slow-but-alive must not page anybody."""
    status = metrics.health(
        snapshot_=_snapshot(backlog=5, running=1,
                            workers={"live": 1, "last_heartbeat_age_seconds": 12}),
        stall_seconds=900)
    assert status["status"] == "healthy"
    assert status["healthy"] is True


def test_recent_completions_mean_the_pipeline_is_healthy_even_with_a_quiet_heartbeat():
    status = metrics.health(
        snapshot_=_snapshot(backlog=5, running=1,
                            throughput={"last_1m": 0, "last_5m": 0, "last_60m": 30},
                            workers={"live": 1, "last_heartbeat_age_seconds": 4000}),
        stall_seconds=900)
    assert status["healthy"] is True


def test_health_against_the_real_database_returns_a_known_status():
    status = pipeline.health()
    assert status["status"] in ("healthy", "idle", "stopped", "stalled")


def test_the_stall_alert_is_never_raised_for_a_healthy_pipeline():
    assert metrics.raise_stall_alert({"healthy": True, "status": "healthy"}) is None


# --- operations routes ---------------------------------------------------------------------------------------

def test_the_three_read_only_routes_are_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/api/document-pipeline/metrics", "/api/document-pipeline/health",
            "/api/document-pipeline/blockers"} <= paths


def test_the_routes_require_documents_view():
    dependency = require_capability("documents.view")
    assert dependency(principal=VIEWER) is VIEWER
    with pytest.raises(HTTPException) as raised:
        dependency(principal=OUTSIDER)
    assert raised.value.status_code == 403


def test_the_metrics_route_returns_the_snapshot():
    import json

    from app.routes.document_pipeline import pipeline_metrics

    payload = json.loads(bytes(pipeline_metrics(_principal=VIEWER).body))
    assert payload["installed"] is True
    assert "backlog" in payload


def test_the_health_route_answers_503_when_the_pipeline_is_not_progressing(monkeypatch):
    from app.routes import document_pipeline as route_module

    monkeypatch.setattr("app.services.document_pipeline_continuous.service.health",
                        lambda: {"healthy": False, "status": "stalled", "reason": "wedged"})
    assert route_module.pipeline_health(_principal=VIEWER).status_code == 503

    monkeypatch.setattr("app.services.document_pipeline_continuous.service.health",
                        lambda: {"healthy": True, "status": "idle", "reason": None})
    assert route_module.pipeline_health(_principal=VIEWER).status_code == 200


def test_the_blockers_route_serves_both_queues():
    import json

    from app.routes.document_pipeline import pipeline_blockers

    blocked = json.loads(bytes(pipeline_blockers(queue="blocked", lane=None, limit=10, offset=0,
                                                 _principal=VIEWER).body))
    review = json.loads(bytes(pipeline_blockers(queue="review", lane=None, limit=10, offset=0,
                                                _principal=VIEWER).body))
    assert blocked["queue"] == "blocked" and isinstance(blocked["rows"], list)
    assert review["queue"] == "review" and isinstance(review["rows"], list)


def test_the_route_module_holds_no_business_logic():
    """Routes compose; they must never become a second place the pipeline's rules live."""
    import pathlib

    source = pathlib.Path("app/routes/document_pipeline.py").read_text()
    for forbidden in ("engine.begin(", ".insert(", ".update(", "write_audit_event", "run_ocr"):
        assert forbidden not in source


# --- configuration posture ------------------------------------------------------------------------------------

def test_the_pipeline_ships_disabled(monkeypatch):
    """Merging this must change no runtime behaviour on any host."""
    from app.config import document_pipeline_enabled

    monkeypatch.delenv("DOCUMENT_PIPELINE_ENABLED", raising=False)
    assert document_pipeline_enabled() is False
    monkeypatch.setenv("DOCUMENT_PIPELINE_ENABLED", "true")
    assert document_pipeline_enabled() is True


def test_the_scheduler_registers_pipeline_jobs_only_when_enabled():
    import inspect

    from app.jobs import scheduler

    source = inspect.getsource(scheduler.start_scheduler)
    assert "document_pipeline_enabled()" in source
    index = source.index("document_pipeline_enabled()")
    assert source.index("document-pipeline-tick") > index
    assert source.index("document-pipeline-monitor") > index
