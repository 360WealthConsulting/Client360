"""Enterprise Read Surface Adoption tests (Phase D.37).

Covers projection-backed reads (a firm-wide read served from a rebuilt+healthy projection), fallback
behavior (unbuilt/stale → authoritative + usage counters), RBAC (a record-scoped principal is never
served a projection), behavior preservation (projection value == authoritative value; runtime/policy
untouched), stale detection (lag beyond the freshness threshold → fallback), adoption governance (clean +
detects each finding), the adoption diagnostics + route, the migration head (D.37 adds no migration), the
route inventory, and the architecture invariants (adoption READS ONLY, never mutates, goes through the
helper — never queries an rm_ table directly in a read surface — and the write side stays authoritative).
"""
import pytest
from sqlalchemy import text

from app.database.projection_tables import READ_MODEL_TABLES
from app.db import engine as db
from app.db import metadata
from app.security.models import Principal
from app.services.analytics import sources
from app.services.events import publisher
from app.services.projections import adoption, engine, governance, registry

metadata.tables["outbox_events"]

FIRM = Principal(1, "firm@e.com", "Firm", frozenset({"record.read_all", "observability.audit"}))
SCOPED = Principal(2, "scoped@e.com", "Scoped", frozenset())


_BASE = 900000  # high id range for test-inserted authoritative rows (cleaned up per test)


@pytest.fixture(autouse=True)
def _reset():
    engine.reset_stats()
    adoption.reset_usage()
    with db.begin() as c:
        for tbl in ("outbox_events", "outbox_processed_events", "outbox_dead_letters", *READ_MODEL_TABLES):
            c.execute(text(f"DELETE FROM {tbl}"))
        c.execute(text("DELETE FROM projects WHERE id >= :b"), {"b": _BASE})
        c.execute(text("UPDATE projection_state SET last_processed_event_id=0, events_processed=0, "
                       "failed_events=0, rebuild_count=0, replay_count=0, health='unbuilt', "
                       "last_validation_ok=NULL, rebuild_history=NULL"))
    yield
    with db.begin() as c:
        c.execute(text("DELETE FROM projects WHERE id >= :b"), {"b": _BASE})
    engine.reset_stats()
    adoption.reset_usage()


def _projects(n, status="active", start=1):
    """Publish n project_created events (builds the projection; does NOT touch the authoritative table)."""
    for i in range(start, start + n):
        publisher.publish("operations.project_created",
                          {"project_id": _BASE + i, "category": "tax", "status": status},
                          subject_ref=f"project:{_BASE + i}")


def _authoritative_active_projects():
    from app.db import projects
    with db.connect() as c:
        return c.scalar(select_count(projects, "status='active'"))


def select_count(table, where_sql):
    return text(f"SELECT count(*) FROM {table.name} WHERE {where_sql}")


# --- fallback (default, behavior-preserving) ---------------------------------

def test_unbuilt_projection_falls_back_and_counts_fallback():
    _projects(3)
    # projection is unbuilt → should_use False → authoritative read (identical value), fallback recorded.
    assert adoption.should_use("operations.projects", FIRM, firm_level=True) is False
    authoritative = _authoritative_active_projects()
    n = sources.active_project_count(FIRM)
    assert n == authoritative  # behavior preserved: the adopted read equals the authoritative read
    stats = adoption.usage_stats()
    assert stats["reads"] == 0 and stats["fallbacks"] >= 1


def test_all_adopted_reads_fall_back_by_default():
    # With no projections built, every adopted analytics read returns its authoritative value and
    # records only fallbacks — behavior is unchanged.
    _projects(2)
    adoption.reset_usage()
    sources.active_project_count(FIRM)
    sources.projection_open_opportunity_count(FIRM)
    sources.projection_tax_return_count(FIRM)
    sources.projection_open_exception_count(FIRM)
    stats = adoption.usage_stats()
    assert stats["reads"] == 0
    assert stats["fallbacks"] >= 4


# --- projection-backed read (firm-wide, healthy) -----------------------------

def test_firm_wide_read_served_from_projection_after_rebuild():
    # The read models are empty at test start (fixture clears them), so the projection count reflects
    # exactly the events published here.
    _projects(4, status="active")
    _projects(1, status="closed", start=100)
    engine.rebuild("operations.projects")
    assert engine.state("operations.projects")["health"] == "healthy"
    assert adoption.should_use("operations.projects", FIRM, firm_level=True) is True
    adoption.reset_usage()
    n = sources.active_project_count(FIRM)
    assert n == 4  # only active, from the projection
    stats = adoption.usage_stats()
    assert stats["reads"] == 1 and stats["fallbacks"] == 0


def test_unbuilt_adopted_reads_equal_authoritative():
    # Behavior preservation by default: with projections unbuilt, each adopted firm read returns the
    # exact authoritative value (the read falls through to the unchanged authoritative query).
    from app.db import exceptions, opportunities, projects
    with db.connect() as c:
        auth_proj = c.scalar(select_count(projects, "status='active'"))
        auth_opp = c.scalar(select_count(opportunities, "status='open'"))
        auth_exc = c.scalar(select_count(exceptions, "status NOT IN ('resolved','cancelled')"))
    assert sources.active_project_count(FIRM) == auth_proj
    assert sources.projection_open_opportunity_count(FIRM) == auth_opp
    assert sources.projection_open_exception_count(FIRM) == auth_exc


# --- RBAC (scoped principal never served a projection) -----------------------

def test_scoped_principal_never_served_projection():
    _projects(3, status="active")
    engine.rebuild("operations.projects")
    # people.summary is a record-scoped read (firm_level=False) — a scoped principal must be refused.
    assert adoption.should_use("people.summary", SCOPED, firm_level=False) is False
    assert adoption.count("people.summary", SCOPED, firm_level=False) is None


def test_scoped_client_count_stays_authoritative():
    # client_count is scoped; a scoped principal must get the authoritative scoped read (no projection).
    adoption.reset_usage()
    sources.client_count(SCOPED)
    stats = adoption.usage_stats()
    # people.summary must not have served a projection read for the scoped principal.
    assert stats["by_projection"].get("people.summary", {}).get("reads", 0) == 0


def test_firm_level_read_allows_any_principal_when_healthy():
    _projects(2, status="active")
    engine.rebuild("operations.projects")
    # operations.projects is firm_level=True → served to any principal (no record scope on the read).
    assert adoption.should_use("operations.projects", SCOPED, firm_level=True) is True


# --- stale detection ---------------------------------------------------------

def test_stale_projection_falls_back():
    _projects(2, status="active")
    engine.rebuild("operations.projects")
    assert adoption.should_use("operations.projects", FIRM, firm_level=True) is True
    # Push lag beyond the freshness threshold without reprocessing → stale → fallback.
    _projects(adoption.FRESHNESS_LAG_THRESHOLD + 5, status="active", start=1000)
    assert engine.lag("operations.projects") > adoption.FRESHNESS_LAG_THRESHOLD
    assert adoption.should_use("operations.projects", FIRM, firm_level=True) is False
    adoption.reset_usage()
    sources.active_project_count(FIRM)
    assert adoption.usage_stats()["fallbacks"] >= 1


# --- diagnostics -------------------------------------------------------------

def test_adoption_diagnostics_shape():
    d = adoption.adoption_diagnostics()
    assert d["adopted_surfaces"] == 12
    assert len(d["targets"]) == 12
    assert d["joins_avoided_total"] == 9
    assert {"usage", "targets", "adopted_surfaces", "joins_avoided_total"} <= set(d)


def test_usage_stats_track_reads_and_fallbacks():
    _projects(2, status="active")
    engine.rebuild("operations.projects")
    adoption.reset_usage()
    sources.active_project_count(FIRM)          # projection read
    sources.projection_tax_return_count(FIRM)   # unbuilt → fallback
    stats = adoption.usage_stats()
    assert stats["reads"] == 1 and stats["fallbacks"] >= 1
    assert stats["projection_read_pct"] is not None


# --- governance --------------------------------------------------------------

def test_adoption_governance_clean():
    report = governance.validate_adoption()
    assert report["ok"] is True, report["findings"]
    assert report["issue_count"] == 0


def test_governance_detects_available_but_unused(monkeypatch):
    # Drop one target from the map → its (still active) projection becomes available-but-unused.
    trimmed = dict(adoption.ADOPTION_TARGETS)
    trimmed.pop("operations.projects")
    monkeypatch.setattr(adoption, "ADOPTION_TARGETS", trimmed)
    report = governance.validate_adoption()
    types = {f["type"] for f in report["findings"]}
    assert "projection_available_but_unused" in types
    assert report["ok"] is False


def test_governance_detects_endpoint_reading_authoritative(monkeypatch):
    # Add a target with no adoption site in the scanned modules → endpoint_reading_authoritative.
    extended = dict(adoption.ADOPTION_TARGETS)
    extended["nonexistent.projection.xyz"] = "analytics.nope"
    monkeypatch.setattr(adoption, "ADOPTION_TARGETS", extended)
    report = governance.validate_adoption()
    types = {f["type"] for f in report["findings"]}
    assert "endpoint_reading_authoritative" in types


def test_governance_detects_stale_projection(monkeypatch):
    _projects(2, status="active")
    engine.rebuild("operations.projects")
    _projects(engine.LAG_THRESHOLD + 5, status="active", start=5000)
    report = governance.validate_adoption()
    types = {f["type"] for f in report["findings"]}
    assert "projection_stale" in types


# --- route inventory ---------------------------------------------------------

def test_adoption_route_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/projections/adoption" in paths


def test_total_route_count():
    from app.main import app
    assert len(app.routes) == 846


def test_adoption_route_returns_report():
    from app.routes.projections import adoption_report

    class _Req:
        pass
    resp = adoption_report(_Req(), principal=FIRM)
    import json
    body = json.loads(bytes(resp.body))
    assert "diagnostics" in body and "governance" in body
    assert body["governance"]["ok"] is True


# --- architecture invariants -------------------------------------------------

def test_read_surface_adoption_added_no_migration():
    # D.37 is code-only — read-surface adoption introduced NO migration (adoption is in-process +
    # code, on top of the D.36 read-model tables). Durable across later phases that do add migrations.
    import pathlib
    versions = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
    names = [p.name for p in versions.glob("*.py")]
    assert not any("read_surface" in n or "surface_adoption" in n for n in names)


def test_adoption_modules_do_not_query_read_model_tables_directly():
    # Every adopted read must go through the helper — an rm_ table referenced directly in a read
    # surface would be a mixed authoritative/projection read (governance flags it).
    import pathlib
    import re
    base = pathlib.Path(__file__).resolve().parents[1]
    for rel in adoption.ADOPTION_MODULES:
        src = (base / rel).read_text()
        # word-boundary rm_<name> — a real read-model table reference (not e.g. "firm_level").
        hits = re.findall(r"\brm_[a-z]\w*", src)
        assert not hits, f"{rel} references read-model tables directly: {hits}"


def test_adoption_never_mutates_projection():
    _projects(3, status="active")
    engine.rebuild("operations.projects")
    before = engine.size("operations.projects")
    for _ in range(5):
        sources.active_project_count(FIRM)
    assert engine.size("operations.projects") == before  # reads never change the read model


def test_adoption_targets_have_distinct_read_functions():
    fns = list(adoption.ADOPTION_TARGETS.values())
    assert len(fns) == len(set(fns))  # no duplicate query implementations


def test_all_targets_have_a_definition():
    defs = {d["projection_id"] for d in registry.list_definitions()}
    assert set(adoption.ADOPTION_TARGETS) <= defs
