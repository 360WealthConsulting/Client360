"""Enterprise Read Models & Projection Engine tests (Phase D.36).

Covers projection creation, rebuild, deterministic replay, reset, incremental processing, duplicate-
event idempotency, lag detection, schema evolution (drift), governance (passes + detects each finding),
diagnostics, analytics, the route inventory, and the architecture invariants (no second event log,
projections never read authoritative tables, read models are disposable, replay fully rebuilds, and no
business behavior changes). Read models are built purely from the outbox events.
"""
import pytest
from sqlalchemy import text

from app.database.projection_tables import READ_MODEL_TABLES
from app.db import engine as db
from app.db import metadata
from app.services.events import publisher
from app.services.projections import diagnostics, engine, governance, registry
from app.services.projections.definitions import PROJECTION_DEFINITIONS

outbox_events = metadata.tables["outbox_events"]


@pytest.fixture(autouse=True)
def _reset():
    # Per-test isolation: the outbox log accumulates across tests, and a rebuild reads the whole log,
    # so clear the event log + the disposable read models + the projection checkpoints before each test.
    engine.reset_stats()
    with db.begin() as c:
        for tbl in ("outbox_events", "outbox_processed_events", "outbox_dead_letters", *READ_MODEL_TABLES):
            c.execute(text(f"DELETE FROM {tbl}"))
        c.execute(text("UPDATE projection_state SET last_processed_event_id=0, events_processed=0, "
                       "failed_events=0, rebuild_count=0, replay_count=0, health='unbuilt', "
                       "last_validation_ok=NULL, rebuild_history=NULL"))
    yield
    engine.reset_stats()


def _publish(event_type, payload, subject_ref=None):
    return publisher.publish(event_type, payload, subject_ref=subject_ref)


def _count(table):
    with db.connect() as c:
        return c.scalar(text(f"SELECT count(*) FROM {table}")) or 0


# --- creation / registry -----------------------------------------------------

def test_registry_seeded():
    defs = registry.list_definitions()
    assert len(defs) == 12
    assert {d["projection_id"] for d in defs} == set(PROJECTION_DEFINITIONS)
    cov = registry.coverage()
    assert cov["active"] == 12 and cov["event_coverage_pct"] == 100.0


def test_definitions_mirror_registry():
    assert {d["projection_id"] for d in registry.list_definitions()} == set(PROJECTION_DEFINITIONS)


# --- rebuild / apply ---------------------------------------------------------

def test_rebuild_opportunity_pipeline():
    _publish("opportunity.created",
             {"opportunity_id": 1, "pipeline_id": 2, "stage_id": 3, "status": "open"}, "opportunity:1")
    _publish("opportunity.stage_changed",
             {"opportunity_id": 1, "to_stage_id": 5, "from_status": "open", "to_status": "open"}, "opportunity:1")
    _publish("opportunity.won", {"opportunity_id": 1, "status": "won"}, "opportunity:1")
    r = engine.rebuild("opportunity.pipeline")
    assert r["processed"] == 3 and engine.size("opportunity.pipeline") == 1
    with db.connect() as c:
        row = dict(c.execute(text("SELECT * FROM rm_opportunity_pipeline")).mappings().one())
    assert row["opportunity_id"] == 1 and row["status"] == "won" and row["stage_id"] == 5
    assert row["closed_at"] is not None


def test_rebuild_exception_dashboard_and_resolve():
    _publish("exception.opened",
             {"exception_id": 9, "code": "BEN_X", "domain": "benefits", "category": "compliance",
              "severity": "high", "status": "open"}, "exception:9")
    _publish("exception.resolved",
             {"exception_id": 9, "resolution_code": "FIXED", "from_status": "open", "to_status": "resolved"},
             "exception:9")
    engine.rebuild("exception.dashboard")
    with db.connect() as c:
        row = dict(c.execute(text("SELECT * FROM rm_exception_dashboard")).mappings().one())
    assert row["exception_id"] == 9 and row["status"] == "resolved" and row["code"] == "BEN_X"
    assert row["opened_at"] is not None and row["resolved_at"] is not None


def test_activity_feed_captures_every_event():
    _publish("people.person_created", {"person_id": 1, "match_method": "m"}, "person:1")
    _publish("document.registered", {"document_id": 2, "classification": "tax", "status": "active"}, "document:2")
    engine.rebuild("activity.feed")
    assert engine.size("activity.feed") == 2
    with db.connect() as c:
        cats = {r["category"] for r in c.execute(text("SELECT category FROM rm_activity_feed")).mappings()}
    assert {"people", "document"} <= cats


# --- replay determinism ------------------------------------------------------

def test_replay_is_deterministic():
    for i in range(3):
        _publish("operations.task_created",
                 {"task_id": i, "project_id": None, "status": "planned", "priority": "normal"}, f"operations_task:{i}")
    v = engine.validate("operations.tasks")
    assert v["deterministic"] is True and v["rows"] == 3
    st = engine.state("operations.tasks")
    assert st["replay_count"] == 0 and st["rebuild_count"] >= 2  # validate rebuilds twice
    assert st["last_validation_ok"] == "ok"


def test_replay_operation_records_replay():
    _publish("operations.task_created",
             {"task_id": 1, "project_id": None, "status": "planned", "priority": "normal"}, "operations_task:1")
    engine.replay("operations.tasks")
    st = engine.state("operations.tasks")
    assert st["replay_count"] == 1 and st["last_replay_duration_ms"] is not None


# --- reset -------------------------------------------------------------------

def test_reset_clears_read_model():
    _publish("operations.task_created",
             {"task_id": 1, "project_id": None, "status": "planned", "priority": "normal"}, "operations_task:1")
    engine.rebuild("operations.tasks")
    assert engine.size("operations.tasks") == 1
    engine.reset("operations.tasks")
    assert engine.size("operations.tasks") == 0
    st = engine.state("operations.tasks")
    assert st["health"] == "unbuilt" and st["last_processed_event_id"] == 0


# --- incremental + idempotency + lag -----------------------------------------

def test_incremental_processing():
    _publish("document.registered", {"document_id": 1, "classification": "tax", "status": "active"}, "document:1")
    engine.rebuild("document.status")
    assert engine.size("document.status") == 1
    _publish("document.status_changed",
             {"document_id": 1, "from_status": "active", "to_status": "review"}, "document:1")
    engine.process("document.status", incremental=True)  # apply only the new event
    with db.connect() as c:
        row = dict(c.execute(text("SELECT * FROM rm_document_status")).mappings().one())
    assert row["status"] == "review"


def test_duplicate_events_are_idempotent():
    # rebuilding twice over the same events yields the same read model (idempotent upserts + feed dedupe)
    _publish("people.person_created", {"person_id": 1, "match_method": "m"}, "person:1")
    _publish("people.person_updated", {"person_id": 1, "changed_fields": ["email"]}, "person:1")
    engine.rebuild("people.summary")
    size1 = engine.size("people.summary")
    engine.rebuild("people.summary")
    assert engine.size("people.summary") == size1 == 1
    engine.rebuild("activity.feed")
    n1 = engine.size("activity.feed")
    engine.rebuild("activity.feed")
    assert engine.size("activity.feed") == n1   # feed dedupes on event_id


def test_lag_detection():
    engine.rebuild("operations.tasks")   # establish (rebuild_count > 0), lag 0
    assert engine.lag("operations.tasks") == 0
    _publish("operations.task_created",
             {"task_id": 1, "project_id": None, "status": "planned", "priority": "normal"}, "operations_task:1")
    assert engine.lag("operations.tasks") == 1   # a new unprocessed event → lag


# --- governance --------------------------------------------------------------

def test_governance_passes():
    report = governance.validate()
    assert report["ok"] is True and report["issue_count"] == 0
    assert report["coverage"]["event_coverage_pct"] == 100.0


def test_governance_detects_missing_owner():
    with db.begin() as c:
        c.execute(text("UPDATE projection_definitions SET owner=NULL WHERE projection_id='people.summary'"))
    try:
        report = governance.validate()
        assert any(f["type"] == "projection_without_owner" and f.get("projection") == "people.summary"
                   for f in report["findings"])
    finally:
        with db.begin() as c:
            c.execute(text("UPDATE projection_definitions SET owner='people' WHERE projection_id='people.summary'"))


def test_governance_detects_schema_drift():
    with db.begin() as c:
        c.execute(text("UPDATE projection_definitions SET schema_version=99 WHERE projection_id='tax.pipeline'"))
    try:
        report = governance.validate()
        assert any(f["type"] == "projection_schema_drift" and f.get("projection") == "tax.pipeline"
                   for f in report["findings"])
    finally:
        with db.begin() as c:
            c.execute(text("UPDATE projection_definitions SET schema_version=1 WHERE projection_id='tax.pipeline'"))


def test_governance_detects_replay_mismatch():
    with db.begin() as c:
        c.execute(text("UPDATE projection_state SET last_validation_ok='mismatch' "
                       "WHERE projection_id='tax.pipeline'"))
    try:
        report = governance.validate()
        assert any(f["type"] == "projection_replay_mismatch" for f in report["findings"])
    finally:
        with db.begin() as c:
            c.execute(text("UPDATE projection_state SET last_validation_ok=NULL WHERE projection_id='tax.pipeline'"))


def test_governance_no_projection_reads_authoritative():
    from app.services.projections.governance import _authoritative_tables_referenced
    assert _authoritative_tables_referenced() == []


# --- diagnostics + analytics -------------------------------------------------

def test_diagnostics_view():
    _publish("insurance.case_created", {"case_id": 1, "case_type": "life", "status": "open"}, "insurance_case:1")
    engine.rebuild("insurance.pipeline")
    diag = diagnostics.diagnostics("insurance.pipeline")
    assert diag["health"] == "healthy" and diag["size"] == 1 and diag["rebuild_count"] >= 1
    assert diag["rebuild_history"]
    h = diagnostics.health()
    assert "healthy" in h["by_health"]


def test_analytics_projection_metrics():
    from app.services.analytics import sources
    from app.services.analytics.metrics import METRICS
    for key in ("projection_count", "healthy_projections", "lagging_projections",
                "projection_events_processed", "projection_avg_latency_ms", "largest_projection_size",
                "projection_rebuilds", "projection_replays", "projection_failures", "projection_coverage"):
        assert key in METRICS
    assert sources.projection_count(None) == 12
    assert sources.projection_coverage_pct(None) == 100.0


# --- architecture invariants -------------------------------------------------

def test_no_second_event_log():
    # the outbox is the sole event log; only read-model + registry tables are added
    assert "event_log" not in metadata.tables and "domain_event_log" not in metadata.tables
    assert {"projection_definitions", "projection_state"} <= set(metadata.tables)


def test_read_models_are_disposable_rebuild_from_events():
    _publish("benefits.enrollment_created",
             {"enrollment_id": 1, "plan_year_id": 2, "coverage_tier": "employee", "status": "elected"},
             "benefit_enrollment:1")
    engine.rebuild("benefits.enrollment")
    assert engine.size("benefits.enrollment") == 1
    # delete the read model entirely, then rebuild from the (untouched) events
    with db.begin() as c:
        c.execute(text("DELETE FROM rm_benefits_enrollment"))
    assert engine.size("benefits.enrollment") == 0
    engine.rebuild("benefits.enrollment")
    assert engine.size("benefits.enrollment") == 1   # fully reconstructed from events


def test_projection_engine_only_reads_outbox_not_authoritative():
    import pathlib
    src = pathlib.Path(engine.__file__).parent.joinpath("definitions.py").read_text()
    # projection handlers only touch rm_* read tables (via _tbl) + the outbox
    import re
    for tbl in re.findall(r"""_tbl\(\s*["']([\w]+)["']""", src):
        assert tbl.startswith("rm_"), tbl


def test_projection_tick_dark_launched():
    from app.config import projections_enabled
    assert projections_enabled() is False


def test_no_business_behavior_change_from_projections():
    # rebuilding a projection touches only its read table — no authoritative row is created/changed
    before = _count("people")
    _publish("people.person_created", {"person_id": 999999, "match_method": "m"}, "person:999999")
    engine.rebuild("people.summary")
    assert _count("people") == before   # the authoritative people table is untouched


def test_projections_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/projections", "/projections/rebuild", "/projections/replay", "/projections/reset",
            "/projections/health", "/projections/governance", "/projections/diagnostics"} <= paths


def test_projections_routes_match_no_middleware_rule():
    from app.security.middleware import RULES
    assert not any(pattern.search("/projections") for pattern, _cap in RULES)
    assert not any(pattern.search("/projections/governance") for pattern, _cap in RULES)
