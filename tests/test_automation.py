"""Enterprise Automation platform tests (Phase D.22).

Covers job CRUD + status, invalid job_type rejection, job templates, job schedules, run execution
+ execution history + audit events, retry policy (attempts + backoff) and failure policy
(dead-letter), worker lifecycle + heartbeats, execution locks (single-flight), authorization +
client-anchored record scope, and the orchestration integrations — Reporting (run a report
schedule + sweep), Workflow (SLA sweep), Communications (send a message), Analytics (snapshot
capture), Microsoft 365 references — plus Timeline lifecycle events (client-anchored only), the
append-only audit ledger, and architecture invariants. The scheduler, outbox, and every dispatched
domain are untouched; the runner tick is gated OFF by default.
"""
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, insert, select, update

from app.db import (
    automation_events,
    automation_execution_locks,
    automation_job_templates,
    automation_jobs,
    automation_retry_policies,
    automation_runs,
    automation_workers,
    engine,
    people,
    record_assignments,
    timeline_events,
    users,
)
from app.security.models import Principal
from app.services.automation import catalog, common, dispatch, runner
from app.services.automation import service as svc

CAPS = frozenset({"automation.view", "automation.manage", "automation.execute", "automation.audit",
                  "automation.admin", "record.read_all", "record.write_all"})


def _sfx():
    return uuid.uuid4().hex[:8]


def _principal(uid, caps=CAPS):
    return Principal(uid, "a@e.test", "A", frozenset(caps))


def _setup():
    tag = _sfx()
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"au-{tag}@e.test", normalized_email=f"au-{tag}@e.test",
            display_name=f"U {tag}", status="active").returning(users.c.id)).scalar_one()
        stranger = c.execute(users.insert().values(
            email=f"str-{tag}@e.test", normalized_email=f"str-{tag}@e.test",
            display_name=f"S {tag}", status="active").returning(users.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"Client {tag}", primary_email=f"{tag}@e.test",
            normalized_email=f"{tag}@e.test", active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(record_assignments).values(
            user_id=uid, entity_type="person", entity_id=pid, assignment_type="owner",
            effective_date=date.today()))
    return {"uid": uid, "stranger": stranger, "pid": pid, "tag": tag}


def _teardown(ids):
    uid = ids["uid"]
    with engine.begin() as c:
        # analytics snapshot-capture jobs write firm snapshots; clean ours to avoid polluting the
        # analytics trend tests (which count points per metric).
        from app.db import analytics_snapshots
        c.execute(delete(analytics_snapshots).where(analytics_snapshots.c.captured_by == uid))
        c.execute(delete(automation_runs).where(automation_runs.c.triggered_by_user_id == uid))
        c.execute(delete(automation_execution_locks).where(automation_execution_locks.c.owner.like(f"manual:{uid}%")))
        c.execute(delete(automation_jobs).where(automation_jobs.c.created_by_user_id == uid))
        c.execute(delete(automation_retry_policies).where(automation_retry_policies.c.created_by_user_id == uid))
        c.execute(delete(automation_job_templates).where(automation_job_templates.c.created_by_user_id == uid))
        c.execute(delete(automation_workers).where(automation_workers.c.code.like(f"manual:{uid}%")))
        c.execute(delete(timeline_events).where(timeline_events.c.source == "automation",
                                                timeline_events.c.person_id == ids["pid"]))
        c.execute(delete(record_assignments).where(record_assignments.c.entity_id == ids["pid"],
                                                   record_assignments.c.entity_type == "person"))
        c.execute(delete(people).where(people.c.id == ids["pid"]))
        c.execute(delete(users).where(users.c.id.in_((uid, ids["stranger"]))))


def _job(p, ids, *, job_type="maintenance", config=None, retry_policy_id=None):
    return svc.create_job(p, code=f"j-{ids['tag']}-{_sfx()}", name="Job", job_type=job_type,
                          config=config, retry_policy_id=retry_policy_id, actor_user_id=ids["uid"])


# --- job CRUD ----------------------------------------------------------------

def test_job_crud_and_invalid_type():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        j = _job(p, ids, job_type="maintenance")
        assert j["status"] == "enabled"
        assert svc.get_job(p, j["id"])["name"] == "Job"
        j = svc.set_job_status(p, j["id"], "disabled", actor_user_id=ids["uid"])
        assert j["status"] == "disabled"
        with pytest.raises(common.AutomationError):
            svc.create_job(p, code=f"bad-{ids['tag']}", name="Bad", job_type="not_a_type",
                           actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


def test_job_templates_and_catalog_seeds():
    ids = _setup()
    try:
        catalog.create_template(code=f"t-{ids['tag']}", name="Nightly", job_type="maintenance",
                                actor_user_id=ids["uid"])
        assert catalog.get_template(code=f"t-{ids['tag']}") is not None
        # default policy/queue seeds present
        assert catalog.get_retry_policy(code="default") is not None
        assert any(q["code"] == "default" for q in catalog.list_queues())
        assert any(fp["code"] == "default" for fp in catalog.list_failure_policies())
    finally:
        _teardown(ids)


def test_schedules_crud():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        j = _job(p, ids)
        s = svc.create_schedule(p, j["id"], name="Daily", frequency="daily",
                                actor_user_id=ids["uid"])
        assert s["active"] is True
        s = svc.set_schedule_active(p, s["id"], False, actor_user_id=ids["uid"])
        assert s["active"] is False
        assert any(x["id"] == s["id"] for x in svc.list_schedules(job_id=j["id"]))
    finally:
        _teardown(ids)


# --- execution + history -----------------------------------------------------

def test_run_maintenance_job_succeeds():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        j = _job(p, ids, job_type="maintenance")
        run = svc.run_job(p, j["id"], actor_user_id=ids["uid"])
        assert run["status"] == "succeeded"
        assert run["result"] == {"maintenance": "ok"}
        assert run["duration_ms"] is not None
        # execution history + audit events
        etypes = [e["event_type"] for e in svc.run_audit(p, run["id"])]
        assert "run_enqueued" in etypes and "run_started" in etypes and "run_succeeded" in etypes
        assert any(r["id"] == run["id"] for r in svc.list_runs(p, job_id=j["id"])["rows"])
    finally:
        _teardown(ids)


def test_failure_policy_dead_letters_without_retry_budget():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        # a job that will fail (report schedule that does not exist); no retry policy -> max 1 attempt
        j = _job(p, ids, job_type="run_report_schedule", config={"schedule_id": 999999999})
        run = svc.run_job(p, j["id"], actor_user_id=ids["uid"])
        assert run["status"] == "dead"        # retry budget exhausted -> failure policy dead-letter
        assert run["attempts"] == 1 and run["last_error"]
    finally:
        _teardown(ids)


def test_retry_policy_schedules_retry_then_dead():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        rp = catalog.create_retry_policy(code=f"r-{ids['tag']}", name="Twice", max_attempts=2,
                                         retry_delays=[60], actor_user_id=ids["uid"])
        j = _job(p, ids, job_type="run_report_schedule", config={"schedule_id": 999999999},
                 retry_policy_id=rp["id"])
        run = svc.run_job(p, j["id"], actor_user_id=ids["uid"])
        assert run["status"] == "pending"     # first failure -> retry scheduled
        assert run["attempts"] == 1 and run["available_at"] is not None
        # simulate the backoff elapsing, then re-execute -> exhausted -> dead
        with engine.begin() as c:
            c.execute(update(automation_runs).where(automation_runs.c.id == run["id"])
                      .values(available_at=datetime.now(UTC) - timedelta(seconds=1)))
        run2 = svc.execute_run(run["id"], worker_code=f"manual:{ids['uid']}")
        assert run2["status"] == "dead" and run2["attempts"] == 2
    finally:
        _teardown(ids)


# --- worker lifecycle + execution locks --------------------------------------

def test_worker_lifecycle_and_heartbeat():
    ids = _setup()
    try:
        w = runner.ensure_worker(code=f"manual:{ids['uid']}:w", name="Test worker")
        assert w["status"] == "active"
        runner.heartbeat(w["id"], active_runs=2, detail={"x": 1})
        hbs = runner.worker_heartbeats(w["id"])
        assert hbs and hbs[0]["active_runs"] == 2
        assert any(x["id"] == w["id"] for x in runner.list_workers())
    finally:
        with engine.begin() as c:
            c.execute(delete(automation_workers).where(automation_workers.c.code == f"manual:{ids['uid']}:w"))
        _teardown(ids)


def test_execution_lock_single_flight():
    ids = _setup()
    try:
        key = f"test-lock-{ids['tag']}"
        with engine.begin() as c:
            assert svc.acquire_lock(c, key, owner=f"manual:{ids['uid']}") is True
            assert svc.acquire_lock(c, key, owner=f"manual:{ids['uid']}") is False  # held
            svc.release_lock(c, key)
            assert svc.acquire_lock(c, key, owner=f"manual:{ids['uid']}") is True   # free again
            svc.release_lock(c, key)
    finally:
        _teardown(ids)


def test_runner_cycle_sweeps_due_schedule():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        j = _job(p, ids, job_type="maintenance")
        svc.create_schedule(p, j["id"], name="Due", frequency="daily", interval_seconds=3600,
                            next_run_at=datetime.now(UTC) - timedelta(minutes=1),
                            actor_user_id=ids["uid"])
        result = runner.run_worker_cycle(worker_code=f"manual:{ids['uid']}:cyc")
        assert result["enqueued"] >= 1 and result["executed"] >= 1
    finally:
        with engine.begin() as c:
            c.execute(delete(automation_runs).where(automation_runs.c.trigger_source == "schedule"))
            c.execute(delete(automation_workers).where(automation_workers.c.code == f"manual:{ids['uid']}:cyc"))
        _teardown(ids)


# --- authorization + record scope --------------------------------------------

def test_client_anchored_run_scope():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        j = _job(p, ids, job_type="maintenance")
        run = svc.run_job(p, j["id"], person_id=ids["pid"], actor_user_id=ids["uid"])
        stranger = _principal(ids["stranger"], {"automation.view"})
        assert svc.get_run(stranger, run["id"]) is None
        assert all(r["id"] != run["id"] for r in svc.list_runs(stranger)["rows"])
    finally:
        _teardown(ids)


def test_client_anchored_run_requires_write_scope():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        j = _job(p, ids, job_type="maintenance")
        stranger = _principal(ids["stranger"], {"automation.execute"})
        with pytest.raises(common.AutomationError):
            svc.run_job(stranger, j["id"], person_id=ids["pid"], actor_user_id=ids["stranger"])
    finally:
        _teardown(ids)


# --- orchestration integrations ----------------------------------------------

def test_reporting_integration_run_and_sweep():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        from app.services.reporting import schedules as reporting_schedules
        from app.services.reporting import service as reporting_svc
        rpt_caps = _principal(ids["uid"], CAPS | {"reporting.view", "reporting.manage"})
        d = reporting_svc.create_dashboard(rpt_caps, code=f"rd-{ids['tag']}", name="D",
                                           actor_user_id=ids["uid"])
        rs = reporting_schedules.create_schedule(rpt_caps, name="RS", dashboard_id=d["id"],
                                                 actor_user_id=ids["uid"])
        j = _job(p, ids, job_type="run_report_schedule", config={"schedule_id": rs["id"]})
        run = svc.run_job(p, j["id"], actor_user_id=ids["uid"])
        assert run["status"] == "succeeded" and run["result"].get("report_id")
    finally:
        with engine.begin() as c:
            from app.db import report_schedules, reporting_dashboards, reports
            c.execute(delete(reports).where(reports.c.created_by_user_id == ids["uid"]))
            c.execute(delete(report_schedules).where(report_schedules.c.created_by_user_id == ids["uid"]))
            c.execute(delete(reporting_dashboards).where(reporting_dashboards.c.created_by_user_id == ids["uid"]))
        _teardown(ids)


def test_workflow_integration_sla_sweep():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        j = _job(p, ids, job_type="workflow_sla_sweep")
        run = svc.run_job(p, j["id"], actor_user_id=ids["uid"])
        assert run["status"] == "succeeded" and "sla" in run["result"]
    finally:
        _teardown(ids)


def test_analytics_snapshot_integration():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        j = _job(p, ids, job_type="capture_analytics_snapshots")
        run = svc.run_job(p, j["id"], actor_user_id=ids["uid"])
        assert run["status"] == "succeeded" and "captured" in run["result"]
    finally:
        _teardown(ids)


def test_communications_integration_send():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        from app.services.communications import service as comms
        conv = comms.create_conversation(_principal(ids["uid"], CAPS | {"communications.send"}),
                                         subject="Auto", actor_user_id=ids["uid"])
        j = _job(p, ids, job_type="send_communication",
                 config={"conversation_id": conv["id"], "body": "Automated message"})
        run = svc.run_job(p, j["id"], actor_user_id=ids["uid"])
        assert run["status"] == "succeeded" and run["result"].get("message_id")
    finally:
        _teardown(ids)


def test_microsoft365_job_types_present():
    # M365 sync is referenced via dispatch (reused, not duplicated) — no Graph call in tests.
    for jt in ("m365_mail_sync", "m365_calendar_sync", "m365_document_sync"):
        assert jt in dispatch.DISPATCH_REGISTRY


# --- timeline integration ----------------------------------------------------

def test_timeline_events_for_client_anchored_run():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        j = _job(p, ids, job_type="maintenance")
        svc.run_job(p, j["id"], person_id=ids["pid"], actor_user_id=ids["uid"])
        # firm-level run -> no timeline event
        svc.run_job(p, j["id"], actor_user_id=ids["uid"])
        with engine.connect() as c:
            types = set(c.scalars(select(timeline_events.c.event_type).where(
                timeline_events.c.source == "automation",
                timeline_events.c.person_id == ids["pid"])))
        assert "automation_job_started" in types
        assert "automation_job_completed" in types
    finally:
        _teardown(ids)


# --- audit ledger + architecture invariants ----------------------------------

def test_audit_ledger_append_only():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        j = _job(p, ids, job_type="maintenance")
        run = svc.run_job(p, j["id"], actor_user_id=ids["uid"])
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(update(automation_events).where(automation_events.c.entity_id == run["id"])
                          .values(event_type="tampered"))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(delete(automation_events).where(automation_events.c.entity_id == run["id"]))
    finally:
        _teardown(ids)


def test_dispatch_registry_covers_all_job_types():
    from app.database.automation_tables import JOB_TYPES
    assert set(dispatch.DISPATCH_REGISTRY) == set(JOB_TYPES)


def test_scheduler_registers_automation_tick_wrapper():
    import app.jobs.scheduler as scheduler
    assert hasattr(scheduler, "run_automation_tick")


def test_route_prefix_matches_no_middleware_rule():
    from app.security.middleware import RULES
    assert not any(pattern.search("/automation") for pattern, _cap in RULES)
    assert not any(pattern.search("/automation/jobs/5") for pattern, _cap in RULES)
