"""Bounded recovery for the 2026-08-29 TaxDome merge-retirement incident.

Every test builds the incident shape through the REAL merge executor, then simulates the TaxDome
resurrection, so the fixtures cannot drift from what actually happened.
"""
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.db import engine
from app.services import document_merge_execute as dx
from app.services import document_merge_recovery as rec

_TAG = "DMRECTEST"
RUN = "dmx-incident-test"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        c.execute(text("DELETE FROM document_events WHERE document_id IN"
                       " (SELECT id FROM documents WHERE original_name LIKE :p)"),
                  {"p": f"{_TAG}%"})
        c.execute(text("DELETE FROM documents WHERE original_name LIKE :p"), {"p": f"{_TAG}%"})
        c.execute(text("DELETE FROM people WHERE full_name LIKE :p"), {"p": f"{_TAG}%"})


def _person(s=""):
    with engine.begin() as c:
        return c.execute(text("INSERT INTO people (full_name) VALUES (:n) RETURNING id"),
                         {"n": f"{_TAG}{s} {uuid.uuid4().hex[:8]}"}).scalar_one()


def _doc(sha, *, person_id=None, source_system="TaxDome Drive", status="active"):
    t = uuid.uuid4().hex[:10]
    tags = json.dumps({"source_system": source_system} if source_system else {})
    with engine.begin() as c:
        return c.execute(text(
            "INSERT INTO documents (original_name, stored_name, storage_path, size_bytes, sha256,"
            " status, archived, person_id, tags) VALUES (:n,:s,:p,1,:sha,:st,false,:pid,"
            " CAST(:tags AS jsonb)) RETURNING id"),
            {"n": f"{_TAG} {t}.pdf", "s": f"rec-{t}", "p": f"/t/{t}", "sha": sha, "st": status,
             "pid": person_id, "tags": tags}).scalar_one()


def _incident(source_system="TaxDome Drive", *, resurrect=True):
    """Build the exact production shape: merge two same-owner TaxDome duplicates through the REAL
    executor, then simulate the TaxDome sync that resurrected the retired row."""
    sha, pid = uuid.uuid4().hex * 2, _person()
    a, b = sorted([_doc(sha, person_id=pid, source_system=source_system),
                   _doc(sha, person_id=pid, source_system=source_system)])
    plan = dx.plan()
    mine = [p for p in plan["partitions"] if p["sha256"] == sha]
    assert len(mine) == 1
    result = dx.apply(plan_doc={**plan, "partitions": mine, "safe_partitions": 1,
                                "rows_to_retire": 1},
                      apply_writes=True, expected_safe_partitions=1, expected_retirement_rows=1,
                      request_id=RUN)
    assert result["status"] == dx.STATUS_SUCCESS
    survivor = result["applied"][0]["survivor_document_id"]
    retired = result["applied"][0]["retired_document_ids"][0]
    run_id = result["run_id"]
    if resurrect:
        _resurrect(retired)
    return {"sha": sha, "person": pid, "survivor": survivor, "retired": retired, "run": run_id}


def _incident_batch(n):
    """ONE merge run retiring n partitions - the real production shape - then resurrect them all."""
    pairs = []
    for _ in range(n):
        sha, pid = uuid.uuid4().hex * 2, _person()
        pairs.append((sha, sorted([_doc(sha, person_id=pid), _doc(sha, person_id=pid)])))
    plan = dx.plan()
    shas = {sha for sha, _ in pairs}
    mine = [p for p in plan["partitions"] if p["sha256"] in shas]
    assert len(mine) == n
    result = dx.apply(plan_doc={**plan, "partitions": mine, "safe_partitions": n,
                                "rows_to_retire": n},
                      apply_writes=True, expected_safe_partitions=n, expected_retirement_rows=n)
    assert result["status"] == dx.STATUS_SUCCESS
    out = []
    for applied in sorted(result["applied"], key=lambda a: a["survivor_document_id"]):
        retired = applied["retired_document_ids"][0]
        _resurrect(retired)
        out.append({"survivor": applied["survivor_document_id"], "retired": retired,
                    "run": result["run_id"]})
    return out


def _resurrect(document_id):
    """Exactly what the pre-fix TaxDome sync did: force active, wipe deleted_at, bump updated_at."""
    with engine.begin() as c:
        c.execute(text("UPDATE documents SET status='active', deleted_at=NULL,"
                       " updated_at=now() + interval '1 minute' WHERE id=:i"), {"i": document_id})


def _window(inc, pad_seconds=600):
    with engine.connect() as c:
        at = c.execute(text("SELECT occurred_at FROM document_events WHERE document_id=:i"
                            " AND event_type='merged_into_canonical' ORDER BY id LIMIT 1"),
                       {"i": inc["retired"]}).scalar_one()
    return at - timedelta(seconds=pad_seconds), at + timedelta(seconds=pad_seconds)


def _state(document_id):
    with engine.connect() as c:
        return dict(c.execute(text("SELECT id,status,deleted_at,updated_at,storage_uri,"
                                   "storage_path,person_id,sha256 FROM documents WHERE id=:i"),
                              {"i": document_id}).mappings().one())


def _plan(inc, **kw):
    ws, we = _window(inc)
    return rec.plan(run_id=kw.pop("run_id", inc["run"]),
                    window_start=kw.pop("window_start", ws),
                    window_end=kw.pop("window_end", we), **kw)


def _apply(inc, **kw):
    p = kw.pop("plan_doc", None) or _plan(inc)
    return rec.apply(plan_doc=p, apply_writes=True,
                     expected_eligible=p["eligible_count"],
                     expected_plan_fingerprint=p["plan_fingerprint"], **kw)


def _events(document_id, event_type):
    with engine.connect() as c:
        return c.execute(text("SELECT count(*) FROM document_events WHERE document_id=:i"
                              " AND event_type=:e"), {"i": document_id, "e": event_type}).scalar_one()


# === the happy path ================================================================================

def test_the_exact_incident_shaped_row_is_recovered():
    inc = _incident()
    before_survivor = _state(inc["survivor"])
    before_retired = _state(inc["retired"])
    assert before_retired["status"] == "active" and before_retired["deleted_at"] is None

    with engine.connect() as c:
        merge_at = c.execute(text("SELECT occurred_at FROM document_events WHERE document_id=:i"
                                  " AND event_type='merged_into_canonical'"),
                             {"i": inc["retired"]}).scalar_one()

    p = _plan(inc)
    assert p["eligible_count"] == 1 and p["refused_count"] == 0
    assert p["eligible"][0]["document_id"] == inc["retired"]
    assert p["eligible"][0]["survivor_document_id"] == inc["survivor"]

    r = _apply(inc, plan_doc=p)
    assert r["status"] == dx.STATUS_SUCCESS and r["documents_restored"] == 1

    after = _state(inc["retired"])
    assert after["status"] == "deleted"
    assert after["deleted_at"] == merge_at, "deleted_at restored to the ORIGINAL merge instant"
    # nothing else about the row moved
    for f in ("storage_uri", "storage_path", "person_id", "sha256"):
        assert after[f] == before_retired[f], f
    assert _state(inc["survivor"]) == before_survivor, "the survivor is never modified"
    assert _events(inc["retired"], "merged_into_canonical") == 1, "original event preserved"
    assert _events(inc["retired"], rec.RECOVERY_EVENT) == 1, "a NEW recovery event is recorded"


def test_the_recovered_document_leaves_the_merge_plan_again():
    inc = _incident()
    assert any(inc["retired"] in p["duplicate_document_ids"] or
               p["survivor_document_id"] == inc["retired"] for p in dx.plan()["partitions"]), \
        "sanity: while resurrected it IS back in the plan"
    _apply(inc)
    assert not any(inc["retired"] in p["duplicate_document_ids"] or
                   p["survivor_document_id"] == inc["retired"] for p in dx.plan()["partitions"])


def test_the_audit_records_the_recovery_without_any_document_content():
    inc = _incident()
    r = _apply(inc)
    with engine.connect() as c:
        raw = c.execute(text("SELECT metadata::text FROM audit_events WHERE action=:a"
                             " ORDER BY id DESC LIMIT 1"),
                        {"a": rec.RECOVERY_AUDIT_ACTION}).scalar()
    md = json.loads(raw)
    for k in ("recovery_run_id", "incident_run_id", "document_id", "survivor_document_id",
              "restored_status", "restored_deleted_at", "observed_status_before",
              "merge_event_id", "deleted_at_source", "guards_passed", "fingerprint"):
        assert k in md, k
    assert md["document_id"] == inc["retired"] and md["restored_status"] == "deleted"
    assert md["recovery_run_id"] == r["recovery_run_id"]
    assert "text" not in md and "fact_value" not in md and "ocr" not in json.dumps(md).lower()


# === guards: each refusal is proven independently ==================================================

def test_an_active_taxdome_row_with_no_merge_evidence_is_untouched():
    pid = _person()
    did = _doc(uuid.uuid4().hex * 2, person_id=pid)
    inc = _incident()
    p = _plan(inc)
    assert did not in [c["document_id"] for c in p["candidates"]], \
        "the population comes from the incident AUDIT, not from 'active rows'"
    _apply(inc, plan_doc=p)
    assert _state(did)["status"] == "active"


def test_an_ordinary_user_soft_delete_is_untouched():
    inc = _incident(resurrect=False)          # correctly deleted, never resurrected
    pid = _person()
    other = _doc(uuid.uuid4().hex * 2, person_id=pid)
    with engine.begin() as c:
        c.execute(text("UPDATE documents SET status='deleted', deleted_at=now() WHERE id=:i"),
                  {"i": other})
    p = _plan(inc)
    assert other not in [c["document_id"] for c in p["candidates"]]
    assert p["eligible_count"] == 0, "an already-correctly-deleted row needs no recovery"
    assert "status_is_active" in p["refusals_by_guard"]


def test_an_archived_row_is_refused():
    inc = _incident()
    with engine.begin() as c:
        c.execute(text("UPDATE documents SET status='archived' WHERE id=:i"), {"i": inc["retired"]})
    p = _plan(inc)
    assert p["eligible_count"] == 0 and "status_is_active" in p["refusals_by_guard"]


def test_a_deliberately_restored_document_outside_the_run_constraints_is_untouched():
    inc = _incident()
    p = _plan(inc, run_id="dmx-some-other-run")
    assert p["population_from_audit"] == 0 and p["eligible_count"] == 0
    _apply(inc, plan_doc=p)
    assert _state(inc["retired"])["status"] == "active", "not our incident: left alone"


def test_the_wrong_merge_run_is_refused():
    inc = _incident()
    ws, we = _window(inc)
    p = rec.plan(run_id="dmx-not-the-incident", window_start=ws, window_end=we)
    assert p["population_from_audit"] == 0 and p["eligible_count"] == 0


def test_an_event_outside_the_incident_window_is_refused():
    inc = _incident()
    ws, we = _window(inc)
    p = rec.plan(run_id=inc["run"], window_start=we + timedelta(hours=1),
                 window_end=we + timedelta(hours=2))
    assert p["eligible_count"] == 0
    assert "event_inside_incident_window" in p["refusals_by_guard"]


def test_multiple_merge_events_are_refused():
    inc = _incident()
    with engine.begin() as c:
        c.execute(text("INSERT INTO document_events (document_id,event_type,from_status,"
                       "to_status,occurred_at) VALUES (:i,'merged_into_canonical','active',"
                       "'deleted',now())"), {"i": inc["retired"]})
    p = _plan(inc)
    assert p["eligible_count"] == 0 and "exactly_one_merge_event" in p["refusals_by_guard"]


def test_a_wrong_transition_is_refused():
    inc = _incident()
    with engine.begin() as c:
        c.execute(text("UPDATE document_events SET from_status='archived' WHERE document_id=:i"
                       " AND event_type='merged_into_canonical'"), {"i": inc["retired"]})
    p = _plan(inc)
    assert p["eligible_count"] == 0
    assert "event_transition_active_to_deleted" in p["refusals_by_guard"]


def test_a_missing_survivor_is_refused():
    inc = _incident()
    with engine.begin() as c:
        c.execute(text("DELETE FROM document_events WHERE document_id=:i"), {"i": inc["survivor"]})
        c.execute(text("DELETE FROM documents WHERE id=:i"), {"i": inc["survivor"]})
    p = _plan(inc)
    assert p["eligible_count"] == 0 and "survivor_exists" in p["refusals_by_guard"]


def test_a_survivor_sha_mismatch_is_refused():
    inc = _incident()
    with engine.begin() as c:
        c.execute(text("UPDATE documents SET sha256=:s WHERE id=:i"),
                  {"s": uuid.uuid4().hex * 2, "i": inc["survivor"]})
    p = _plan(inc)
    assert p["eligible_count"] == 0 and "survivor_sha256_matches" in p["refusals_by_guard"]


def test_a_broken_ownership_partition_invariant_is_refused():
    inc = _incident()
    with engine.begin() as c:                 # the survivor now belongs to a different client
        c.execute(text("UPDATE documents SET person_id=:p WHERE id=:i"),
                  {"p": _person(" MOVED"), "i": inc["survivor"]})
    p = _plan(inc)
    assert p["eligible_count"] == 0
    assert "ownership_partition_invariant_holds" in p["refusals_by_guard"]


def test_an_unexpected_dependency_is_refused():
    inc = _incident()
    with engine.begin() as c:                 # the row has been used since it was resurrected
        c.execute(text("INSERT INTO document_ocr (document_id,status,text,char_count)"
                       " VALUES (:i,'completed','t',1)"), {"i": inc["retired"]})
    p = _plan(inc)
    assert p["eligible_count"] == 0 and "no_unexpected_dependencies" in p["refusals_by_guard"]
    assert p["candidates"][0]["unexpected_dependencies"]["document_ocr"] == 1


def test_a_non_taxdome_source_system_is_refused():
    inc = _incident(source_system="SharePoint")
    p = _plan(inc)
    assert p["eligible_count"] == 0 and "source_system_is_taxdome" in p["refusals_by_guard"]


def test_a_document_not_updated_after_the_merge_is_refused():
    inc = _incident()
    with engine.connect() as c:
        at = c.execute(text("SELECT occurred_at FROM document_events WHERE document_id=:i"
                            " AND event_type='merged_into_canonical'"),
                       {"i": inc["retired"]}).scalar_one()
    with engine.begin() as c:                 # active, but never touched by the sync
        c.execute(text("UPDATE documents SET updated_at=:t WHERE id=:i"),
                  {"t": at - timedelta(seconds=1), "i": inc["retired"]})
    p = _plan(inc)
    assert p["eligible_count"] == 0 and "updated_after_merge_event" in p["refusals_by_guard"]


# === dry run, guards, idempotency, staleness, interruption =========================================

_MUTATING = ("insert", "update", "delete", "truncate", "merge", "copy", "create", "drop", "alter")


def _record_sql():
    from sqlalchemy import event
    seen = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", _before)
    return seen, lambda: event.remove(engine, "before_cursor_execute", _before)


def _mutating(statements):
    return [s.strip()[:140] for s in statements
            if any(" ".join(s.strip().split()).lower().startswith(v) for v in _MUTATING)]


def _snapshot():
    with engine.begin() as c:
        return {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
                for t in ("documents", "document_events", "audit_events")}


def test_a_dry_run_issues_no_mutating_sql_at_all():
    inc = _incident()
    before = _snapshot()
    seen, stop = _record_sql()
    try:
        r = rec.apply(plan_doc=_plan(inc))          # dry run
    finally:
        stop()
    assert r["dry_run"] is True and r["wrote_anything"] is False
    assert r["status"] == dx.STATUS_DRY_RUN and r["exit_code"] == 0
    assert r["documents_planned"] == 1
    assert _mutating(seen) == [], f"dry run issued mutating SQL: {_mutating(seen)}"
    assert _snapshot() == before
    assert _state(inc["retired"])["status"] == "active", "nothing changed"


def test_the_plan_itself_writes_nothing():
    inc = _incident()
    before = _snapshot()
    seen, stop = _record_sql()
    try:
        p = _plan(inc)
    finally:
        stop()
    assert p["read_only"] is True and p["wrote_anything"] is False
    assert _mutating(seen) == []
    assert _snapshot() == before


def test_apply_without_the_expected_guards_is_refused_before_any_write():
    inc = _incident()
    before = _snapshot()
    p = _plan(inc)
    for kw in ({}, {"expected_eligible": 1}, {"expected_plan_fingerprint": p["plan_fingerprint"]}):
        with pytest.raises(rec.RecoveryError, match="requires"):
            rec.apply(plan_doc=p, apply_writes=True, **kw)
    assert _snapshot() == before and _state(inc["retired"])["status"] == "active"


def test_a_count_or_fingerprint_mismatch_is_refused_before_any_write():
    inc = _incident()
    p = _plan(inc)
    before = _snapshot()
    with pytest.raises(rec.RecoveryError, match="expectation mismatch"):
        rec.apply(plan_doc=p, apply_writes=True, expected_eligible=99,
                  expected_plan_fingerprint=p["plan_fingerprint"])
    with pytest.raises(rec.RecoveryError, match="expectation mismatch"):
        rec.apply(plan_doc=p, apply_writes=True, expected_eligible=p["eligible_count"],
                  expected_plan_fingerprint="0" * 64)
    assert _snapshot() == before and _state(inc["retired"])["status"] == "active"


def test_a_missing_run_or_window_is_refused_outright():
    for kw in ({"run_id": None}, {"window_start": None}, {"window_end": None}):
        with pytest.raises(rec.RecoveryError, match="explicit incident run id and time window"):
            rec.plan(**{"run_id": "x", "window_start": datetime.now(UTC),
                        "window_end": datetime.now(UTC), **kw})


def test_stale_state_between_planning_and_apply_is_refused():
    inc = _incident()
    p = _plan(inc)
    assert p["eligible_count"] == 1
    with engine.begin() as c:                    # the row moves after planning
        c.execute(text("UPDATE documents SET updated_at=now() + interval '1 hour' WHERE id=:i"),
                  {"i": inc["retired"]})
    r = rec.apply(plan_doc=p, apply_writes=True, expected_eligible=1,
                  expected_plan_fingerprint=p["plan_fingerprint"])
    assert r["documents_restored"] == 0 and r["documents_refused"] == 1
    assert r["refused"][0]["refused"] == "StaleStateError"
    assert r["status"] == dx.STATUS_FAILED and r["exit_code"] == 4
    assert _state(inc["retired"])["status"] == "active"


def test_a_row_someone_already_deleted_between_plan_and_apply_is_refused():
    inc = _incident()
    p = _plan(inc)
    with engine.begin() as c:
        c.execute(text("UPDATE documents SET status='deleted', deleted_at=now() WHERE id=:i"),
                  {"i": inc["retired"]})
    r = rec.apply(plan_doc=p, apply_writes=True, expected_eligible=1,
                  expected_plan_fingerprint=p["plan_fingerprint"])
    assert r["documents_restored"] == 0 and r["documents_refused"] == 1
    assert "status_is_active" in r["refused"][0]["detail"]


def test_a_second_apply_changes_zero_rows():
    inc = _incident()
    first = _apply(inc)
    assert first["documents_restored"] == 1
    after_first = _state(inc["retired"])
    snap = _snapshot()

    second_plan = _plan(inc)
    assert second_plan["eligible_count"] == 0, "already restored: nothing eligible"
    second = rec.apply(plan_doc=second_plan, apply_writes=True, expected_eligible=0,
                       expected_plan_fingerprint=second_plan["plan_fingerprint"])
    assert second["documents_restored"] == 0
    assert _state(inc["retired"]) == after_first, "not even updated_at churns"
    assert _snapshot() == snap, "no second event, no second audit row"
    assert _events(inc["retired"], rec.RECOVERY_EVENT) == 1


def test_a_partial_apply_reports_partial_and_exit_3(monkeypatch):
    incidents = _incident_batch(4)                 # ONE run, four retired documents
    ws, we = _window(incidents[0])
    p = rec.plan(run_id=incidents[0]["run"], window_start=ws, window_end=we)
    assert p["eligible_count"] == 4
    real = rec._restore
    calls = {"n": 0}

    def _boom(conn, candidate, recovery_run, incident_run):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("simulated failure")
        return real(conn, candidate, recovery_run, incident_run)

    monkeypatch.setattr(rec, "_restore", _boom)
    r = rec.apply(plan_doc=p, apply_writes=True, batch_size=2, expected_eligible=4,
                  expected_plan_fingerprint=p["plan_fingerprint"])
    assert r["status"] == dx.STATUS_PARTIAL and r["exit_code"] == 3
    assert r["committed_batches"] == [1] and r["failed_batch"]["batch"] == 2
    assert r["documents_restored"] == 2
    order = [c["document_id"] for c in p["eligible"]]
    assert _state(order[0])["status"] == "deleted"
    assert _state(order[1])["status"] == "deleted"
    assert _state(order[2])["status"] == "active", "batch 2 rolled back completely"
    assert _state(order[3])["status"] == "active", "batch 3 never attempted"


def test_a_keyboard_interrupt_is_reported_not_swallowed_as_success(monkeypatch):
    incidents = _incident_batch(2)
    ws, we = _window(incidents[0])
    p = rec.plan(run_id=incidents[0]["run"], window_start=ws, window_end=we)

    def _interrupt(conn, candidate, recovery_run, incident_run):
        raise KeyboardInterrupt()

    monkeypatch.setattr(rec, "_restore", _interrupt)
    try:
        r = rec.apply(plan_doc=p, apply_writes=True, batch_size=1, expected_eligible=2,
                      expected_plan_fingerprint=p["plan_fingerprint"])
    except BaseException as exc:                       # noqa: BLE001 - the point of the test
        raise AssertionError(f"apply() let {type(exc).__name__} escape") from None
    assert r["status"] == dx.STATUS_FAILED and r["exit_code"] == 4
    assert r["failed_batch"]["error"] == "KeyboardInterrupt"
    assert all(_state(i["retired"])["status"] == "active" for i in incidents)


def test_no_filesystem_or_content_capability_exists():
    import ast
    import pathlib
    banned = {"unlink", "remove", "rmtree", "move", "rename", "system", "popen", "run"}
    for rel in ("app/services/document_merge_recovery.py",
                "scripts/recover_taxdome_merge_retirements.py"):
        src = pathlib.Path(rel).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                for n in node.names:
                    assert n.name.split(".")[0] not in {"shutil", "subprocess", "os", "glob",
                                                        "socket", "requests", "httpx"}, n.name
            if isinstance(node, ast.Attribute) and node.attr in banned:
                raise AssertionError(f"{rel} calls .{node.attr}()")
        assert "taxdome_drive" not in src, "recovery must never invoke TaxDome sync"
        assert "DELETE FROM documents" not in src


def test_only_status_and_deleted_at_are_written_on_documents():
    import pathlib
    src = pathlib.Path("app/services/document_merge_recovery.py").read_text(encoding="utf-8")
    updates = [ln for ln in src.splitlines() if "UPDATE documents" in ln]
    assert len(updates) == 1
    stmt = src[src.index("UPDATE documents"):]
    stmt = stmt[:stmt.index("WHERE id")]
    for col in ("person_id", "household_id", "organization_id", "sha256", "storage_uri",
                "storage_path", "storage_provider", "tags", "category", "classification"):
        assert col not in stmt, f"{col} must never be written by recovery"
    assert "status = 'deleted'" in stmt and "deleted_at = :at" in stmt


def test_the_cli_defaults_to_dry_run_and_requires_the_incident_arguments(capsys):
    import scripts.recover_taxdome_merge_retirements as cli
    inc = _incident()
    ws, we = _window(inc)
    before = _snapshot()
    code = cli.main(["--incident-run", inc["run"], "--window-start", ws.isoformat(),
                     "--window-end", we.isoformat()])
    out = capsys.readouterr().out
    assert code == 0
    assert "DRY RUN - NO DATABASE WRITE WAS ISSUED" in out
    assert "no --apply: no document was changed" in out
    assert out.isascii()
    assert _snapshot() == before
    with pytest.raises(SystemExit):
        cli.main([])                                   # the incident arguments are required
