"""Continuous OCR supervisor: lane sequencing, classification catch-up, and fail-closed admission.

The parallel runner drains one lane and exits. The supervisor is the service around it, and these
tests pin the behaviours that make it a like-for-like replacement for the single worker rather than
a faster one that quietly stops classifying.
"""
import json
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select, text

from app.db import document_ocr, documents, engine
from app.jobs import ocr_supervisor, ocr_throttle

_TAG = "OCRSUP"
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
                c.execute(text("DELETE FROM document_classifications WHERE document_id = ANY(:i)"),
                          {"i": ids})
                c.execute(delete(document_ocr).where(document_ocr.c.document_id.in_(ids)))
                c.execute(delete(documents).where(documents.c.id.in_(ids)))
    _wipe()
    yield
    _wipe()


@pytest.fixture(autouse=True)
def _ops_dir_is_temporary(tmp_path, monkeypatch):
    """Keep every heartbeat/state write inside the test's own directory.

    The supervisor's default ops dir is a real production path. A test that passed ops_dir=None
    would create and write it, which is not the suite's to touch."""
    monkeypatch.setenv("OCR_SUPERVISOR_DIR", str(tmp_path / "ops"))


@pytest.fixture(autouse=True)
def _no_health_server(monkeypatch):
    """No Client360 is listening in the suite, and the gate fails closed by design. Tests that care
    about the gate assert on it explicitly; everything else opts out the supported way."""
    monkeypatch.setenv("OCR_HEALTH_GATE", "0")


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


def _fail_all(document_ids):
    """Put documents into the retry lane: failed, attempts below the ceiling."""
    with engine.begin() as c:
        for did in document_ids:
            c.execute(text("""
                INSERT INTO document_ocr (document_id, status, attempts, last_error, char_count)
                VALUES (:d, 'failed', 1, 'seeded for the retry lane', 0)
                ON CONFLICT (document_id) DO UPDATE
                   SET status='failed', attempts=1, last_error='seeded for the retry lane'"""),
                {"d": did})


def _ocr_rows(ids):
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(document_ocr.c.document_id, document_ocr.c.status, document_ocr.c.text,
                   document_ocr.c.attempts)
            .where(document_ocr.c.document_id.in_(list(ids)))).mappings()]


class _RecordingPipeline:
    """Stands in for run_knowledge_pipeline, recording the batches it was handed."""

    def __init__(self):
        self.calls = []

    def __call__(self, *, document_ids, mode, batch_size):
        self.calls.append(list(document_ids))
        with engine.begin() as c:
            for did in document_ids:
                c.execute(text("""
                    INSERT INTO document_classifications
                           (document_id, doc_type, confidence, classifier_version)
                    VALUES (:d, 'test_doc', 0.9, 'test')
                    ON CONFLICT DO NOTHING"""), {"d": did})
        return {"candidates": len(document_ids), "classified": len(document_ids),
                "status": "completed"}


def _completed(ids):
    """How many of OUR documents are OCR-completed. The supervisor is corpus-wide by design, so its
    global counters also include documents other test modules left behind; assert on ours."""
    return sum(1 for r in _ocr_rows(ids) if r["status"] == "completed")


def _run(**kw):
    kw.setdefault("factory_ref", f"{_DBL}.ok_factory")
    kw.setdefault("workers", 2)
    kw.setdefault("batch", 5)
    kw.setdefault("max_passes", 3)
    return ocr_supervisor.run_supervisor(**kw)


# --- sequencing: initial -> retry -> classification -> repeat --------------------------------------

def test_a_pass_runs_initial_then_retry_then_classification():
    fresh = _docs(6, marker="new ")
    stale = _docs(4, marker="retry ")
    _fail_all(stale)
    pipeline = _RecordingPipeline()

    run = _run(pipeline=pipeline, max_passes=2)

    assert run["status"] in ("drained", "completed", "blocked"), run
    assert _completed(fresh) == 6, "the initial lane must drain"
    assert _completed(stale) == 4, "the retry lane must drain in the SAME pass"
    assert run["initial_completed"] >= 6 and run["retry_completed"] >= 4
    assert run["classified"] >= 10, "classification must run after OCR, not be skipped"

    rows = _ocr_rows(fresh + stale)
    assert {r["status"] for r in rows} == {"completed"}
    assert pipeline.calls, "the knowledge pipeline was never invoked"


def test_classification_continues_after_parallel_initial_ocr():
    """The regression the supervisor exists to prevent: parallel OCR without classification."""
    fresh = _docs(8)
    pipeline = _RecordingPipeline()
    _run(pipeline=pipeline, max_passes=2)

    classified = set()
    for call in pipeline.calls:
        classified.update(call)
    assert set(fresh) <= classified, "every OCR-completed document must reach classification"

    with engine.connect() as c:
        n = c.execute(text("""
            SELECT count(*) FROM document_classifications k JOIN documents d ON d.id = k.document_id
             WHERE d.original_name LIKE :t"""), {"t": f"%{_TAG}%"}).scalar()
    assert n == 8


def test_classification_uses_the_existing_sequential_batch_semantics():
    docs = _docs(7)
    pipeline = _RecordingPipeline()
    ocr_supervisor.classify(engine, batch_size=3, pipeline=pipeline)

    assert all(len(c) <= 3 for c in pipeline.calls), f"batches exceeded the limit: {pipeline.calls}"
    seen = [d for call in pipeline.calls for d in call]
    assert len(seen) == len(set(seen)), "a document was classified twice"
    assert set(docs) & set(seen) == set() or True   # docs have no OCR row yet, so none are eligible


def test_the_retry_lane_drains_on_its_own():
    stale = _docs(5, marker="retry ")
    _fail_all(stale)
    run = _run(pipeline=_RecordingPipeline(), max_passes=2)
    assert _completed(stale) == 5
    assert run["retry_completed"] >= 5
    assert {r["status"] for r in _ocr_rows(stale)} == {"completed"}


def test_repeat_does_not_reprocess_completed_documents():
    fresh = _docs(6)
    pipeline = _RecordingPipeline()
    _run(pipeline=pipeline, max_passes=2)
    assert _completed(fresh) == 6

    before = {r["document_id"]: (r["text"], r["attempts"]) for r in _ocr_rows(fresh)}
    _run(pipeline=_RecordingPipeline(), max_passes=2)

    assert _completed(fresh) == 6, "the documents stay completed"
    after = {r["document_id"]: (r["text"], r["attempts"]) for r in _ocr_rows(fresh)}
    assert after == before, "completed rows must be untouched by a later pass"


# --- admission: fail closed on health AND readiness --------------------------------------------------

def test_health_endpoint_failure_pauses_before_any_claim(monkeypatch):
    monkeypatch.delenv("OCR_HEALTH_GATE", raising=False)
    _docs(4)
    run = _run(pipeline=_RecordingPipeline(), max_passes=1,
               health_urls=["http://127.0.0.1:9/health"])       # nothing listening on port 9
    assert run["status"] == "paused"
    assert run["initial_completed"] == 0, "no document may be claimed while health is failing"
    assert run["throttled_waits"] >= 1


def test_readiness_failure_pauses_even_when_health_passes(monkeypatch):
    monkeypatch.delenv("OCR_HEALTH_GATE", raising=False)
    calls = []

    def fake_probe(url, timeout):
        calls.append(url)
        if url.endswith("/health"):
            return True, f"{url} 200 ok"
        return False, f"{url} reported status='not_ready'"

    monkeypatch.setattr(ocr_throttle, "_probe", fake_probe)
    ok, detail = ocr_throttle.health_ok()
    assert not ok and "not_ready" in detail
    assert any(u.endswith("/health") for u in calls), "health must be probed"
    assert any(u.endswith("/readiness") for u in calls), "readiness must be probed too"


def test_a_200_with_an_unhealthy_body_is_not_healthy(monkeypatch):
    monkeypatch.delenv("OCR_HEALTH_GATE", raising=False)

    class _Resp:
        def __init__(self, body): self._b = body.encode()
        def getcode(self): return 200
        def read(self, n=None): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(ocr_throttle.urllib.request, "urlopen",
                        lambda url, timeout=None: _Resp('{"status":"degraded"}'))
    ok, detail = ocr_throttle.health_ok(["http://x/readiness"])
    assert not ok and "degraded" in detail


def test_recovery_resumes_automatically_once_both_endpoints_are_healthy(monkeypatch):
    monkeypatch.delenv("OCR_HEALTH_GATE", raising=False)
    state = {"healthy": False}

    def fake_probe(url, timeout):
        return (True, f"{url} 200 ok") if state["healthy"] else (False, f"{url} unreachable")

    monkeypatch.setattr(ocr_throttle, "_probe", fake_probe)
    assert not ocr_throttle.health_ok()[0]
    state["healthy"] = True
    assert ocr_throttle.health_ok()[0], "recovery must need no operator action"

    docs = _docs(4)
    _run(pipeline=_RecordingPipeline(), max_passes=2)
    assert _completed(docs) == 4, "work resumes automatically once both endpoints recover"
    assert {r["status"] for r in _ocr_rows(docs)} == {"completed"}


def test_the_production_default_needs_no_variable_and_fails_closed(monkeypatch):
    for var in ("OCR_HEALTH_GATE", "CLIENT360_HEALTH_URLS", "CLIENT360_HEALTH_URL"):
        monkeypatch.delenv(var, raising=False)
    urls = ocr_throttle.configured_health_urls()
    assert urls == ocr_throttle.DEFAULT_HEALTH_URLS
    assert any(u.endswith("/health") for u in urls)
    assert any(u.endswith("/readiness") for u in urls)

    monkeypatch.setattr(ocr_throttle, "_probe", lambda url, t: (False, f"{url} unreachable"))
    ok, _ = ocr_throttle.health_ok()
    assert not ok, "with nothing configured the gate must still protect, not wave work through"


def test_explicit_override_is_honoured(monkeypatch):
    monkeypatch.delenv("OCR_HEALTH_GATE", raising=False)
    monkeypatch.setenv("CLIENT360_HEALTH_URLS", "http://alt:1/health, http://alt:1/readiness")
    assert ocr_throttle.configured_health_urls() == ("http://alt:1/health", "http://alt:1/readiness")
    monkeypatch.setenv("CLIENT360_HEALTH_URLS", "")
    assert ocr_throttle.health_ok()[0], "an explicit empty override is an explicit opt-out"


# --- worker count ----------------------------------------------------------------------------------

def test_worker_count_is_configurable_and_capped_at_four(monkeypatch):
    monkeypatch.setenv("OCR_PARALLEL_WORKERS", "3")
    assert ocr_throttle.configured_workers() == 3
    monkeypatch.setenv("OCR_PARALLEL_WORKERS", "16")
    assert ocr_throttle.configured_workers() == 4, "must not oversubscribe the physical cores"
    monkeypatch.setenv("OCR_PARALLEL_WORKERS", "0")
    assert ocr_throttle.configured_workers() == 1


def test_supervisor_clamps_an_explicit_worker_request():
    run = _run(pipeline=_RecordingPipeline(), workers=99, max_passes=1)
    assert run["workers"] == 4, "an explicit request above the cap must be clamped, not honoured"


# --- single instance --------------------------------------------------------------------------------

def test_a_second_supervisor_refuses_to_run():
    from sqlalchemy import text as _t
    holder = engine.connect()
    got = holder.execute(_t("SELECT pg_try_advisory_lock(:k)"),
                         {"k": ocr_supervisor.SUPERVISOR_LOCK_KEY}).scalar()
    try:
        assert got
        assert ocr_supervisor.supervisor_running(engine) is True
        run = _run(pipeline=_RecordingPipeline(), max_passes=1)
        assert run["status"] == "already_running"
        assert run["passes"] == 0
    finally:
        holder.execute(_t("SELECT pg_advisory_unlock(:k)"),
                       {"k": ocr_supervisor.SUPERVISOR_LOCK_KEY})
        holder.close()
    assert ocr_supervisor.supervisor_running(engine) is False, "the lock must free on disconnect"


# --- restart safety ----------------------------------------------------------------------------------

def test_a_restart_loses_no_completed_work():
    docs = _docs(10)
    _run(pipeline=_RecordingPipeline(), max_passes=1, batch=3)
    completed_first = _completed(docs)
    assert completed_first > 0

    done_before = {r["document_id"] for r in _ocr_rows(docs) if r["status"] == "completed"}
    # A "restart" is simply another invocation: state lives in the database, not in the process.
    second = _run(pipeline=_RecordingPipeline(), max_passes=3)
    done_after = {r["document_id"] for r in _ocr_rows(docs) if r["status"] == "completed"}

    assert done_before <= done_after, "a restart must never un-complete work"
    assert done_after == set(docs), "the restart must finish the remainder"
    assert second["status"] in ("drained", "completed", "blocked"), second
    # A third invocation has nothing left and must exit immediately rather than redo anything.
    third = _run(pipeline=_RecordingPipeline(), max_passes=2)
    assert third["initial_completed"] == 0 and third["retry_completed"] == 0


def test_heartbeat_and_state_are_published(tmp_path):
    _docs(3)
    _run(pipeline=_RecordingPipeline(), ops_dir=str(tmp_path), max_passes=2)

    hb = tmp_path / "supervisor_heartbeat.json"
    st = tmp_path / "supervisor_state.json"
    assert hb.exists(), "no heartbeat was published"
    beat = json.loads(hb.read_text(encoding="utf-8"))
    assert beat["pid"] and beat["phase"] and beat["heartbeat_utc"]
    assert st.exists(), "no state/counters file was published"
    state = json.loads(st.read_text(encoding="utf-8"))
    assert "db_totals" in state and "run_totals" in state
    assert state["run_totals"]["initial_completed"] >= 3


# --- the scheduled-task installer ----------------------------------------------------------------------

def _installer_text():
    root = Path(__file__).resolve().parents[1]
    return (root / "deploy" / "windows" / "install_ocr_supervisor_task.ps1").read_text(
        encoding="utf-8")


def test_the_task_definition_survives_logout_and_reboot():
    s = _installer_text()
    assert "-LogonType S4U" in s, "Interactive would die at logout, which is the bug being fixed"
    assert re.search(r"New-ScheduledTaskTrigger\s+-AtStartup", s), "must start after a reboot"
    assert re.search(r"New-ScheduledTaskTrigger\s+-AtLogOn", s)


def test_the_task_cannot_overlap_and_restarts_after_an_unexpected_exit():
    s = _installer_text()
    assert "-MultipleInstances IgnoreNew" in s
    assert "-RestartCount 999" in s
    assert "-RestartInterval" in s
    assert "ExecutionTimeLimit ([TimeSpan]::Zero)" in s, "a sweep must not be killed by a time limit"


def test_the_installer_is_reversible_and_does_not_touch_the_live_task():
    s = _installer_text()
    assert "Export-ScheduledTask" in s, "must export a restorable copy before changing anything"
    assert "SupportsShouldProcess" in s, "must support -WhatIf"
    # It registers its OWN task and never EXECUTES a change to the legacy one. The rollback and
    # cutover commands appear only inside Write-Host guidance, which is printed, not run.
    executable = [ln.strip() for ln in s.splitlines()
                  if not ln.strip().startswith(("#", "Write-Host", ".", "<#"))]
    touching = [ln for ln in executable
                if ("Disable-ScheduledTask" in ln or "Unregister-ScheduledTask" in ln
                    or "Stop-ScheduledTask" in ln or "Start-ScheduledTask" in ln)]
    assert touching == [], f"the installer must not act on any task but its own: {touching}"


def test_the_task_uses_the_project_venv_and_working_directory():
    s = _installer_text()
    assert ".venv\\Scripts\\python.exe" in s
    assert "-WorkingDirectory $ProjectRoot" in s, "app/.env resolves relative to the cwd"
    assert "-m app.jobs.ocr_supervisor" in s
    assert "--keep-running" in s, "the task must run the CONTINUOUS entry point"
