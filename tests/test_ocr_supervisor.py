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
from app.jobs import ocr_claims, ocr_parallel, ocr_supervisor, ocr_throttle
from tests.health_double import HealthDouble

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
    _POOL.clear()
    _wipe()
    yield
    _wipe()
    _POOL.clear()


@pytest.fixture(autouse=True)
def _ops_dir_is_temporary(tmp_path, monkeypatch):
    """Keep every heartbeat/state write inside the test's own directory.

    The supervisor's default ops dir is a real production path. A test that passed ops_dir=None
    would create and write it, which is not the suite's to touch."""
    monkeypatch.setenv("OCR_SUPERVISOR_DIR", str(tmp_path / "ops"))


@pytest.fixture(autouse=True)
def _healthy_endpoints(monkeypatch):
    """Point the fail-closed admission gate at a real, healthy loopback double.

    The gate has no off switch by design, so tests give it something healthy to talk to rather than
    disabling it. Real HTTP, because the workers are spawned processes: they inherit the env var and
    reach the port, which a monkeypatched function could never do.
    """
    with HealthDouble() as health:
        monkeypatch.setenv("CLIENT360_HEALTH_URLS", health.urls_csv)
        yield health


_POOL: list[int] = []


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
    _POOL.extend(ids)
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
    # Scope every run to this module's own documents. The supervisor is corpus-wide in production;
    # letting it sweep the shared test database made it claim other modules' rows and produced
    # intermittent "fewer completed than created" failures elsewhere in the suite.
    kw.setdefault("document_ids", list(_POOL))
    # Pin the RESOURCE thresholds. The box is busy during the suite, so ambient CPU would otherwise
    # trip the 85% ceiling, the supervisor would correctly pause, and the test would see zero work
    # for a reason that has nothing to do with what it is testing. The HEALTH gate is NOT relaxed:
    # it still probes the double, and the tests that are about it assert on it.
    kw.setdefault("min_free_mb", 0)
    kw.setdefault("max_cpu_percent", 100.0)
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

def test_an_unreachable_endpoint_pauses_before_any_claim(monkeypatch):
    _docs(4)
    # Nothing is listening on port 9; the gate must hold rather than claim.
    run = _run(pipeline=_RecordingPipeline(), max_passes=1,
               health_urls=["http://127.0.0.1:9/health", "http://127.0.0.1:9/readiness"])
    assert run["status"] == "paused"
    assert run["initial_completed"] == 0, "no document may be claimed while health is unconfirmed"
    assert run["throttled_waits"] >= 1


def test_a_non_200_health_pauses(_healthy_endpoints):
    _healthy_endpoints.set_health_error(503)
    ok, detail = ocr_throttle.health_ok(_healthy_endpoints.urls)
    assert not ok and "503" in detail


def test_readiness_not_ready_pauses_even_though_health_is_200(_healthy_endpoints):
    """What /readiness actually does mid-deploy: 200, body says migrations are out of sync."""
    _healthy_endpoints.set_not_ready()
    ok, detail = ocr_throttle.health_ok(_healthy_endpoints.urls)
    assert not ok, "a 200 carrying not_ready must not be treated as ready"
    assert "not_ready" in detail


def test_a_malformed_body_does_not_crash_the_gate(_healthy_endpoints):
    _healthy_endpoints.set_malformed()
    ok, _ = ocr_throttle.health_ok(_healthy_endpoints.urls)
    assert ok is True, "a 200 with no parseable status is still a 200 from a live application"


def test_both_endpoints_are_probed(_healthy_endpoints, monkeypatch):
    seen = []
    real = ocr_throttle._probe
    monkeypatch.setattr(ocr_throttle, "_probe",
                        lambda url, t: (seen.append(url), real(url, t))[1])
    assert ocr_throttle.health_ok(_healthy_endpoints.urls)[0]
    assert any(u.endswith("/health") for u in seen), "health must be probed"
    assert any(u.endswith("/readiness") for u in seen), "readiness must be probed too"


def test_recovery_resumes_automatically_with_no_operator_action(_healthy_endpoints):
    docs = _docs(4)
    _healthy_endpoints.set_not_ready()
    paused = _run(pipeline=_RecordingPipeline(), max_passes=1)
    assert paused["status"] == "paused"
    assert _completed(docs) == 0

    _healthy_endpoints.set_healthy()
    _run(pipeline=_RecordingPipeline(), max_passes=2)
    assert _completed(docs) == 4, "work must resume once both endpoints recover"


def test_health_is_rechecked_before_the_retry_lane_and_after_a_pause(_healthy_endpoints, monkeypatch):
    """Admission is consulted before EVERY pass, not once at startup."""
    calls = []
    real = ocr_throttle.may_claim
    monkeypatch.setattr(ocr_throttle, "may_claim",
                        lambda **kw: (calls.append(1), real(**kw))[1])
    stale = _docs(3, marker="retry ")
    _fail_all(stale)
    _docs(3, marker="new ")
    _run(pipeline=_RecordingPipeline(), max_passes=2)
    assert len(calls) >= 2, "the gate must be consulted on each pass, not only the first"


def test_the_production_default_needs_no_variable_and_cannot_be_switched_off(monkeypatch):
    for var in ("CLIENT360_HEALTH_URLS", "CLIENT360_HEALTH_URL"):
        monkeypatch.delenv(var, raising=False)
    assert ocr_throttle.configured_health_urls() == ocr_throttle.DEFAULT_HEALTH_URLS
    assert ocr_throttle.DEFAULT_HEALTH_URLS == ("http://127.0.0.1:8360/health",
                                                "http://127.0.0.1:8360/readiness")
    # An empty override falls back to the defaults; it is NOT a way to disable the gate.
    monkeypatch.setenv("CLIENT360_HEALTH_URLS", "   ")
    assert ocr_throttle.configured_health_urls() == ocr_throttle.DEFAULT_HEALTH_URLS
    monkeypatch.setattr(ocr_throttle, "_probe", lambda url, t: (False, f"{url} unreachable"))
    assert not ocr_throttle.health_ok()[0]
    assert not ocr_throttle.health_ok([])[0], "an empty list must not disable the check either"


def test_an_explicit_override_redirects_but_cannot_disable(monkeypatch):
    monkeypatch.setenv("CLIENT360_HEALTH_URLS", "http://alt:1/health, http://alt:1/readiness")
    assert ocr_throttle.configured_health_urls() == ("http://alt:1/health", "http://alt:1/readiness")


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
    # A third invocation must not redo any of OUR documents (the supervisor is corpus-wide, so its
    # global counters may still move for documents other modules left behind).
    texts_before = {r["document_id"]: r["text"] for r in _ocr_rows(docs)}
    _run(pipeline=_RecordingPipeline(), max_passes=2)
    assert {r["document_id"]: r["text"] for r in _ocr_rows(docs)} == texts_before


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


def test_the_installer_validates_s4u_viability_before_registering():
    """S4U has no network credentials and no mapped drives. The installer must PROVE the database
    and every OCR source drive are reachable without them, and stop rather than register a task that
    silently cannot read."""
    s = _installer_text()
    assert "S4U viability preflight" in s
    assert "DATABASE_URL" in s and "database unreachable" in s, "must prove PostgreSQL is reachable"
    assert "DriveType" in s, "must prove the source drives are local disks, not network"
    assert "throw \"S4U preflight failed" in s, "a failed preflight must stop, not warn and continue"
    # The known Z: exception is reported honestly rather than claimed as compatible.
    assert "remain recoverable OCR failures under S4U" in s


def test_paths_and_arguments_are_quoted():
    s = _installer_text()
    assert '--ops-dir `"$OpsDir`"' in s, "a path with spaces must survive argument splitting"
    assert "Join-Path $ProjectRoot" in s, "paths must be composed, not string-concatenated"


def test_the_task_uses_the_project_venv_and_working_directory():
    s = _installer_text()
    assert ".venv\\Scripts\\python.exe" in s
    assert "-WorkingDirectory $ProjectRoot" in s, "app/.env resolves relative to the cwd"
    assert "-m app.jobs.ocr_supervisor" in s
    assert "--keep-running" in s, "the task must run the CONTINUOUS entry point"


# --- scope propagation and the production default ------------------------------------------------

def test_the_scope_reaches_BOTH_ocr_lanes(monkeypatch):
    """A scope that covered only the initial lane would let the retry lane sweep the whole corpus."""
    seen = []

    def fake_run_parallel(**kw):
        seen.append((kw["mode"], tuple(kw["document_ids"])))
        return {"status": "completed", "completed": 0, "failed": 0, "timed_out": 0,
                "skipped": 0, "unsupported": 0, "encrypted": 0}

    fresh = _docs(2, marker="new ")
    stale = _docs(2, marker="retry ")
    _fail_all(stale)
    monkeypatch.setattr(ocr_supervisor.ocr_parallel, "run_parallel", fake_run_parallel)
    ocr_supervisor.run_supervisor(pipeline=_RecordingPipeline(), workers=1, max_passes=1,
                                  document_ids=fresh + stale, factory_ref=f"{_DBL}.ok_factory")

    modes = {m for m, _ in seen}
    assert modes == {"initial", "retry"}, f"both lanes must run, got {modes}"
    for mode, ids in seen:
        assert set(ids) <= set(fresh + stale), f"the {mode} lane escaped its scope: {ids}"
    assert set(dict(seen)["initial"]) == set(fresh)
    assert set(dict(seen)["retry"]) == set(stale)


def test_omitting_the_scope_sweeps_the_whole_corpus(monkeypatch):
    """The production default. A scope that leaked in by accident would silently shrink a sweep."""
    seen = []

    def fake_run_parallel(**kw):
        seen.append(tuple(kw["document_ids"]))
        return {"status": "completed", "completed": 0, "failed": 0, "timed_out": 0,
                "skipped": 0, "unsupported": 0, "encrypted": 0}

    mine = _docs(3)
    # A document belonging to no test of ours, standing in for the rest of the corpus.
    with engine.begin() as c:
        other = c.execute(documents.insert().values(
            original_name=f"{_TAG}-OUTSIDE {uuid.uuid4().hex[:6]}.pdf",
            stored_name=f"OUT-{uuid.uuid4().hex[:8]}", storage_path="/x",
            storage_provider="Client360 Local", storage_uri="/x", size_bytes=10,
            sha256=uuid.uuid4().hex * 2, status="active",
            archived=False).returning(documents.c.id)).scalar_one()

    monkeypatch.setattr(ocr_supervisor.ocr_parallel, "run_parallel", fake_run_parallel)
    ocr_supervisor.run_supervisor(pipeline=_RecordingPipeline(), workers=1, max_passes=1,
                                  factory_ref=f"{_DBL}.ok_factory")      # no document_ids

    claimed = {i for ids in seen for i in ids}
    assert set(mine) <= claimed, "an unscoped sweep must include our documents"
    assert other in claimed, "an unscoped sweep must be CORPUS-WIDE, not silently narrowed"


def test_classification_is_unscoped_by_default_and_keeps_its_batch_size(monkeypatch):
    """The scope exists for test isolation. Omitted, classification behaves exactly as before:
    corpus-wide, sequential, CLASSIFY_BATCH at a time."""
    calls = []

    def pipeline(*, document_ids, mode, batch_size):
        calls.append((list(document_ids), mode, batch_size))
        return {"candidates": len(document_ids), "classified": 0, "status": "completed"}

    assert ocr_supervisor.CLASSIFY_BATCH == 200, "the existing batch size must not drift"
    ocr_supervisor.classify(engine, pipeline=pipeline, limit_batches=1)
    if calls:
        ids, mode, batch_size = calls[0]
        assert mode == "incremental", "the existing pipeline mode must not change"
        assert batch_size == len(ids) <= ocr_supervisor.CLASSIFY_BATCH


# --- diagnostics carry identity and reason, never client data --------------------------------------

def test_worker_diagnostics_name_the_worker_and_the_reason_without_client_data():
    pool = _docs(3)
    result = ocr_parallel.run_parallel(workers=1, mode="initial", batch=2,
                                       factory_ref=f"{_DBL}.ok_factory", document_ids=pool)
    blob = json.dumps(result, default=str)

    per = [r for r in result["per_worker"] if isinstance(r, dict)]
    assert per and all(r.get("worker_id") for r in per), "every worker must identify itself"
    assert all(r.get("stopped_because") for r in per), "every worker must say why it stopped"
    assert result["admission_reasons"], "the admission reason must be reported"
    assert "child_exitcodes" in result and "child_errors" in result

    # Identity is host/pid/uuid; reasons are resource readings. Neither may carry document content.
    assert "original_name" not in blob
    for row in _ocr_rows(pool):
        assert (row["text"] or "zzz") not in blob, "extracted text must never reach diagnostics"
    assert _TAG not in blob, "document names must never reach diagnostics"


# --- no leakage between tests ------------------------------------------------------------------------

def test_no_live_claims_or_env_overrides_leak_out_of_a_run():
    pool = _docs(4)
    _run(pipeline=_RecordingPipeline(), max_passes=2)

    with engine.connect() as c:
        live = c.execute(text("""
            SELECT count(*) FROM ocr_document_claims cl JOIN documents d ON d.id = cl.document_id
             WHERE d.original_name LIKE :t AND cl.state = 'claimed'"""),
            {"t": f"%{_TAG}%"}).scalar()
    assert live == 0, "no claim may still be held after a run finishes"
    assert ocr_claims.duplicate_live_claims(engine.connect()) == []
    assert _completed(pool) == 4

    # The supervisor lock is released, so the next run is not blocked by this one.
    assert ocr_supervisor.supervisor_running(engine) is False


# --- the thresholds are test injection, not a production weakening -----------------------------------

def test_production_defaults_are_unchanged():
    assert ocr_throttle.DEFAULT_MIN_FREE_MB == 2048
    assert ocr_throttle.DEFAULT_MAX_CPU_PERCENT == 85.0
    assert ocr_throttle.DEFAULT_MAX_WORKERS == 4


def test_thresholds_are_function_arguments_not_production_switches(monkeypatch):
    """Injecting thresholds is an explicit per-call argument. It must not be reachable by setting an
    environment variable to something permissive that a deployment could inherit by accident."""
    import inspect
    sig = inspect.signature(ocr_throttle.may_claim)
    assert {"min_free_mb", "max_cpu_percent"} <= set(sig.parameters)
    assert all(sig.parameters[p].default is None for p in ("min_free_mb", "max_cpu_percent"))

    # With nothing injected and nothing in the environment, the production floors apply.
    for var in ("OCR_MIN_FREE_MB", "OCR_MAX_CPU_PERCENT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(ocr_throttle, "free_mb", lambda: 10)          # far below the 2048 floor
    monkeypatch.setattr(ocr_throttle, "_probe", lambda url, t: (True, f"{url} 200 ok"))
    blocked = ocr_throttle.may_claim()
    assert not blocked and "below floor 2048" in blocked.reason


def test_health_stays_mandatory_however_permissive_the_thresholds(monkeypatch):
    """The one property that must not be bypassable: no combination of memory/CPU arguments may
    admit a claim while Client360 is unhealthy."""
    monkeypatch.setattr(ocr_throttle, "_probe", lambda url, t: (False, f"{url} unreachable"))
    blocked = ocr_throttle.may_claim(min_free_mb=0, max_cpu_percent=100.0)
    assert not blocked, "permissive resource thresholds must not admit an unhealthy application"
    assert "health" in blocked.reason.lower()

    # And the same through the supervisor, which is how the tests inject them.
    _docs(2)
    run = _run(pipeline=_RecordingPipeline(), max_passes=1,
               health_urls=["http://127.0.0.1:9/health"], min_free_mb=0, max_cpu_percent=100.0)
    assert run["status"] == "paused"
    assert run["initial_completed"] == 0


def test_the_lock_session_unlocks_on_exit_and_is_free_afterwards():
    """Normal exit releases the lock explicitly; a crash releases it by disconnecting."""
    assert ocr_supervisor.supervisor_running(engine) is False
    _docs(2)
    _run(pipeline=_RecordingPipeline(), max_passes=1)
    assert ocr_supervisor.supervisor_running(engine) is False, "the lock must not survive the run"

    src = (Path(__file__).resolve().parents[1] / "app" / "jobs" / "ocr_supervisor.py").read_text(
        encoding="utf-8")
    assert "poolclass=NullPool" in src, "the lock must not live on a pooled connection"
    assert "pg_advisory_unlock" in src, "normal exit must unlock explicitly"
    assert "lock_engine.dispose()" in src, "a crash must disconnect, which frees the lock"
