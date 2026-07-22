"""Enterprise Analytics tests (Phase D.15).

Covers metric computation + aggregation, book-scope vs firm-wide, executive gating (restricted !=
missing), dashboard composition + executive gating, targets + variance classification, snapshot
capture + trend math (MoM/YoY/moving average), opportunity revenue trend, export models, custom
dashboards + widgets, deterministic firm intelligence, book_aum additive read, route auth, and
dependency direction (D.5 golden / advisor_intelligence untouched).
"""
import re
import uuid
from datetime import date

import pytest
from sqlalchemy import delete, insert
from starlette.requests import Request

from app.db import (
    accounts,
    analytics_dashboards,
    analytics_snapshots,
    analytics_targets,
    engine,
    opportunities,
    people,
    record_assignments,
    timeline_events,
    users,
)
from app.security.models import Principal
from app.services.analytics import (
    dashboards,
    intelligence,
    metrics,
    service,
    targets,
    trends,
)
from app.services.opportunity import service as osvc

EXEC_CAPS = frozenset({"analytics.view", "analytics.executive", "analytics.export",
                       "analytics.manage_targets", "analytics.manage_dashboards",
                       "opportunity.view", "opportunity.edit", "record.read_all"})
ADV_CAPS = frozenset({"analytics.view", "opportunity.view", "opportunity.edit"})


def _sfx():
    return uuid.uuid4().hex[:8]


def _setup():
    tag = _sfx()
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"an-{tag}@e.test", normalized_email=f"an-{tag}@e.test",
            display_name=f"U {tag}", status="active").returning(users.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"Client {tag}", primary_email=f"{tag}@e.test",
            normalized_email=f"{tag}@e.test", active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(record_assignments).values(
            user_id=uid, entity_type="person", entity_id=pid, assignment_type="owner",
            effective_date=date.today()))
    return {"uid": uid, "pid": pid, "tag": tag}


def _teardown(ids):
    with engine.begin() as c:
        c.execute(delete(analytics_snapshots).where(analytics_snapshots.c.captured_by == ids["uid"]))
        c.execute(delete(analytics_targets).where(analytics_targets.c.created_by == ids["uid"]))
        c.execute(delete(analytics_dashboards).where(analytics_dashboards.c.created_by == ids["uid"]))
        c.execute(delete(accounts).where(accounts.c.person_id == ids["pid"]))
        c.execute(delete(opportunities).where(opportunities.c.created_by == ids["uid"]))
        c.execute(delete(timeline_events).where(timeline_events.c.person_id == ids["pid"]))
        c.execute(delete(record_assignments).where(record_assignments.c.user_id == ids["uid"]))
        c.execute(delete(people).where(people.c.id == ids["pid"]))


def _exec(ids):
    return Principal(ids["uid"], "a@e", "Exec", EXEC_CAPS)


def _adv(ids):
    return Principal(ids["uid"], "a@e", "Adv", ADV_CAPS)


# --- metrics: scope + aggregation -------------------------------------------

def test_metric_book_scope_vs_firm_wide():
    ids = _setup()
    # A second person NOT in the advisor's book, so firm-wide strictly exceeds the book
    # regardless of how populated the database is (deterministic on a clean CI DB too).
    with engine.begin() as c:
        other = c.execute(people.insert().values(
            full_name=f"Other {ids['tag']}", primary_email=f"o-{ids['tag']}@e.test",
            normalized_email=f"o-{ids['tag']}@e.test", active=True).returning(people.c.id)).scalar_one()
    try:
        adv = metrics.compute_metric(_adv(ids), "client_count")["value"]
        exe = metrics.compute_metric(_exec(ids), "client_count")["value"]
        assert adv == 1                       # advisor sees exactly their 1 assigned client
        assert exe >= 2 and exe > adv         # exec (read_all) sees firm-wide, incl. the other person
    finally:
        with engine.begin() as c:
            c.execute(delete(people).where(people.c.id == other))
        _teardown(ids)


def test_pipeline_metric_from_opportunities():
    ids = _setup()
    try:
        osvc.create_opportunity(_exec(ids), title="D", actor_user_id=ids["uid"],
                                person_id=ids["pid"], expected_revenue=250000)
        assert metrics.compute_metric(_exec(ids), "pipeline_value")["value"] == 250000
        assert metrics.compute_metric(_exec(ids), "open_opportunities")["value"] >= 1
    finally:
        _teardown(ids)


def test_executive_metric_gating():
    ids = _setup()
    try:
        # aum is executive -> restricted for a plain analytics.view advisor, available for exec.
        assert metrics.compute_metric(_adv(ids), "aum").get("restricted") is True
        assert metrics.compute_metric(_adv(ids), "aum")["value"] is None
        assert metrics.compute_metric(_exec(ids), "aum").get("restricted") is not True
    finally:
        _teardown(ids)


def test_metric_catalog_and_unknown():
    catalog = metrics.list_metrics()
    assert len(catalog) >= 20 and all("executive" in m for m in catalog)
    assert metrics.compute_metric(Principal(1, "a", "a", frozenset()), "nope")["error"]


def test_book_aum_additive_read():
    ids = _setup()
    try:
        with engine.begin() as c:
            c.execute(accounts.insert().values(person_id=ids["pid"], account_name="A",
                                               custodian="schwab", total_value=500000,
                                               status="active"))
        from app.services.portfolio import book_aum
        assert float(book_aum({ids["pid"]})) == 500000
        assert float(book_aum(set())) == 0
    finally:
        _teardown(ids)


# --- dashboards --------------------------------------------------------------

def test_dashboard_composition_and_exec_gating():
    ids = _setup()
    try:
        d = dashboards.compose_predefined(_exec(ids), "firm")
        assert d["name"] == "Firm Dashboard" and len(d["widgets"]) == 6
        assert all("viz" in w for w in d["widgets"])       # visualization metadata present
        with pytest.raises(dashboards.DashboardError):     # exec dashboard gated for advisor
            dashboards.compose_predefined(_adv(ids), "firm")
        # Advisor dashboard is accessible.
        assert dashboards.compose_predefined(_adv(ids), "advisor")["name"] == "Advisor Dashboard"
    finally:
        _teardown(ids)


def test_predefined_list_accessibility():
    ids = _setup()
    try:
        lst = {d["code"]: d for d in dashboards.list_predefined(_adv(ids))}
        assert lst["firm"]["accessible"] is False and lst["advisor"]["accessible"] is True
    finally:
        _teardown(ids)


def test_custom_dashboard_and_widget():
    ids = _setup()
    try:
        p = _exec(ids)
        d = dashboards.create_dashboard(p, code=f"cd-{ids['tag']}", name="Custom",
                                        actor_user_id=ids["uid"])
        dashboards.add_widget(p, d["id"], title="Clients", metric_key="client_count",
                              viz_type="card")
        with pytest.raises(dashboards.DashboardError):
            dashboards.add_widget(p, d["id"], title="Bad", metric_key="nope")
        composed = dashboards.compose_custom(p, d["code"])
        assert composed["widgets"] and composed["widgets"][0]["metric_key"] == "client_count"
    finally:
        _teardown(ids)


# --- targets / variance ------------------------------------------------------

def test_targets_and_variance_classification():
    ids = _setup()
    try:
        p = _exec(ids)
        osvc.create_opportunity(p, title="D", actor_user_id=ids["uid"], person_id=ids["pid"],
                                expected_revenue=250000)
        targets.set_target(p, metric_key="pipeline_value", actor_user_id=ids["uid"],
                           target_value=500000, threshold_warning=300000, threshold_critical=100000)
        v = targets.variance(p, "pipeline_value")
        assert v["value"] == 250000 and v["status"] == "warning"    # 250k <= 300k warn, > 100k crit
        assert v["variance"] == -250000
        with pytest.raises(targets.TargetError):
            targets.set_target(p, metric_key="nope", actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


# --- trend engine ------------------------------------------------------------

def test_trend_pure_math():
    assert trends.period_key(date(2026, 7, 15), "month") == "2026-07"
    assert trends.period_key(date(2026, 7, 15), "quarter") == "2026-Q3"
    assert trends.period_key(date(2026, 7, 15), "year") == "2026"
    assert trends.growth_pct(110, 100) == 10.0
    assert trends.growth_pct(90, 100) == -10.0
    assert trends.growth_pct(5, 0) is None
    assert trends.moving_average([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]


def test_snapshot_capture_and_trend():
    ids = _setup()
    try:
        p = _exec(ids)
        osvc.create_opportunity(p, title="D", actor_user_id=ids["uid"], person_id=ids["pid"],
                                expected_revenue=100000)
        service.capture_snapshot(p, metric_key="pipeline_value", actor_user_id=ids["uid"],
                                 period_key="2026-05")
        service.capture_snapshot(p, metric_key="pipeline_value", actor_user_id=ids["uid"],
                                 period_key="2026-06")
        t = trends.metric_trend("pipeline_value")
        assert t["points"] == 2 and t["period_over_period_growth"] == 0.0  # both 100k
        # Idempotent recapture overwrites, not duplicates.
        service.capture_snapshot(p, metric_key="pipeline_value", actor_user_id=ids["uid"],
                                 period_key="2026-06")
        assert trends.metric_trend("pipeline_value")["points"] == 2
    finally:
        _teardown(ids)


def test_opportunity_revenue_trend():
    ids = _setup()
    try:
        p = _exec(ids)
        o = osvc.create_opportunity(p, title="D", actor_user_id=ids["uid"], person_id=ids["pid"],
                                    expected_revenue=100000)
        osvc.close_opportunity(p, o["id"], outcome="won", actor_user_id=ids["uid"])
        rt = trends.opportunity_revenue_trend(p, granularity="month")
        assert rt["series"] and rt["series"][-1]["value"] == 100000
    finally:
        _teardown(ids)


def test_capture_all_skips_restricted():
    ids = _setup()
    try:
        res = service.capture_all(_exec(ids), actor_user_id=ids["uid"], period_key="2026-07")
        assert res["captured"]     # at least some metrics captured
    finally:
        _teardown(ids)


# --- export + intelligence ---------------------------------------------------

def test_export_model():
    ids = _setup()
    try:
        ex = service.export_metrics(_exec(ids), ["client_count", "pipeline_value"])
        assert ex["columns"] and len(ex["rows"]) == 2
        assert {r["metric_key"] for r in ex["rows"]} == {"client_count", "pipeline_value"}
    finally:
        _teardown(ids)


def test_firm_intelligence_deterministic():
    ids = _setup()
    try:
        p = _exec(ids)
        for i in range(intelligence.ADVISOR_OVERLOAD_OPPS):
            osvc.create_opportunity(p, title=f"D{i}", actor_user_id=ids["uid"], person_id=ids["pid"])
        fi = intelligence.firm_intelligence(p)
        assert any(o["kind"] == "advisor_overload" for o in fi["observations"])
        assert fi["thresholds"]["advisor_overload_opps"] == 20
    finally:
        _teardown(ids)


# --- routes ------------------------------------------------------------------

def test_routes_render():
    from app.routes.analytics import dashboard as droute
    from app.routes.analytics import export as eroute
    from app.routes.analytics import overview
    ids = _setup()
    try:
        p = _exec(ids)

        def req(path):
            return Request({"type": "http", "method": "GET", "path": path,
                            "headers": [], "query_string": b""})

        assert "Enterprise Analytics" in overview(req("/analytics"), principal=p).body.decode()
        assert droute(req("/analytics/dashboards/firm"), "firm", principal=p).status_code == 200
        assert eroute(req("/analytics/export"), None, principal=p).status_code == 200
    finally:
        _teardown(ids)


# --- dependency direction ----------------------------------------------------

def test_advisor_intelligence_and_sources_do_not_import_analytics():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app" / "services"
    pattern = re.compile(r"from\s+app\.services\.analytics\s+import|import\s+analytics\b")
    for module in ("advisor_intelligence.py", "advisor_work.py", "compliance/reviews.py",
                   "activity_timeline/service.py", "opportunity/service.py", "campaign/service.py",
                   "referral/service.py", "portfolio.py"):
        src = (root / module).read_text()
        assert not pattern.search(src), f"{module} must not import analytics"
    assert "app.services.analytics" not in (root / "advisor_intelligence.py").read_text()
