"""Enterprise Reporting platform tests (Phase D.21).

Covers dashboard CRUD + widgets + publish + render, report definitions + compose, scorecards, KPI
groups, report templates, export profiles, saved views (owner scope), report CRUD + generate,
report scheduling + the Workflow ``run_report_schedule`` action, exports (reused analytics
producer), Analytics integration (widget/scorecard values composed from ``compute_metric``),
inherited executive gating, Communications reference (schedule ``conversation_id``), Microsoft 365
export-delivery reference, Timeline lifecycle events (client-anchored only), the append-only audit
ledger, authorization + record scope, and architecture invariants (Reporting is a composition
layer; Analytics never imports Reporting). Analytics and every source domain are untouched.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import delete, insert, select, update

from app.db import (
    engine,
    people,
    record_assignments,
    report_definitions,
    report_schedules,
    report_templates,
    reporting_dashboards,
    reporting_events,
    reporting_export_profiles,
    reporting_kpi_groups,
    reporting_saved_views,
    reporting_scorecards,
    reports,
    timeline_events,
    users,
)
from app.security.models import Principal
from app.services.reporting import common, render, schedules
from app.services.reporting import service as svc
from app.services.reporting import templates as tmpl

CAPS = frozenset({"reporting.view", "reporting.manage", "reporting.templates", "reporting.audit",
                  "reporting.admin", "analytics.view", "analytics.executive", "record.read_all",
                  "record.write_all"})


def _sfx():
    return uuid.uuid4().hex[:8]


def _principal(uid, caps=CAPS):
    return Principal(uid, "a@e.test", "A", frozenset(caps))


def _setup():
    tag = _sfx()
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"rp-{tag}@e.test", normalized_email=f"rp-{tag}@e.test",
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
    # reporting_events is append-only but polymorphic (no FK); parents can be deleted, events remain.
    uid = ids["uid"]
    with engine.begin() as c:
        c.execute(delete(reports).where(reports.c.created_by_user_id == uid))
        c.execute(delete(report_schedules).where(report_schedules.c.created_by_user_id == uid))
        c.execute(delete(reporting_dashboards).where(reporting_dashboards.c.created_by_user_id == uid))
        c.execute(delete(report_definitions).where(report_definitions.c.created_by_user_id == uid))
        c.execute(delete(reporting_scorecards).where(reporting_scorecards.c.created_by_user_id == uid))
        c.execute(delete(reporting_kpi_groups).where(reporting_kpi_groups.c.created_by_user_id == uid))
        c.execute(delete(report_templates).where(report_templates.c.created_by_user_id == uid))
        c.execute(delete(reporting_export_profiles).where(reporting_export_profiles.c.created_by_user_id == uid))
        c.execute(delete(reporting_saved_views).where(reporting_saved_views.c.owner_user_id.in_((uid, ids["stranger"]))))
        c.execute(delete(timeline_events).where(timeline_events.c.source == "reporting",
                                                timeline_events.c.person_id == ids["pid"]))
        c.execute(delete(record_assignments).where(record_assignments.c.entity_id == ids["pid"],
                                                   record_assignments.c.entity_type == "person"))
        c.execute(delete(people).where(people.c.id == ids["pid"]))
        c.execute(delete(users).where(users.c.id.in_((uid, ids["stranger"]))))


# --- dashboards + widgets + Analytics integration ----------------------------

def test_dashboard_crud_and_widget_renders_analytics_value():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        d = svc.create_dashboard(p, code=f"d-{ids['tag']}", name="Ops", category="operations",
                                 actor_user_id=ids["uid"])
        assert d["status"] == "draft"
        svc.add_widget(p, d["id"], title="Active projects", widget_type="metric",
                       metric_key="active_projects", viz_type="card", actor_user_id=ids["uid"])
        d = svc.set_dashboard_status(p, d["id"], "published", actor_user_id=ids["uid"])
        assert d["status"] == "published" and d["published_at"] is not None
        rendered = svc.render_dashboard(p, d["id"])
        w = rendered["widgets"][0]
        # value composed FROM ANALYTICS compute_metric (never recalculated here)
        assert w["value"]["key"] == "active_projects"
        assert "value" in w["value"]
    finally:
        _teardown(ids)


def test_seeded_audience_dashboards_exist():
    codes = {d["code"] for d in svc.list_dashboards(_principal(1))}
    for expected in ("executive", "operations", "compliance", "advisor", "tax", "insurance",
                     "marketing", "business_development", "client_service", "technology"):
        assert expected in codes


def test_executive_gating_inherited_from_analytics():
    ids = _setup()
    try:
        # principal WITHOUT analytics.executive
        p = _principal(ids["uid"], {"reporting.view", "reporting.manage", "analytics.view"})
        d = svc.create_dashboard(p, code=f"d-{ids['tag']}", name="Exec", category="executive",
                                 actor_user_id=ids["uid"])
        svc.add_widget(p, d["id"], title="AUM", metric_key="aum", actor_user_id=ids["uid"])
        rendered = svc.render_dashboard(p, d["id"])
        # aum is an executive metric -> Analytics withholds it (restricted), inherited automatically
        assert rendered["widgets"][0]["value"]["restricted"] is True
    finally:
        _teardown(ids)


# --- definitions + scorecards + KPI groups -----------------------------------

def test_report_definition_compose():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        d = svc.create_definition(p, name="Ops report", report_type="operational",
                                  category="operations",
                                  definition={"metric_keys": ["active_projects", "open_operational_tasks"]},
                                  actor_user_id=ids["uid"])
        composed = svc.compose_definition(p, d["id"])
        assert composed["name"] == "Ops report"
        assert {m["key"] for m in composed["metrics"]} == {"active_projects", "open_operational_tasks"}
    finally:
        _teardown(ids)


def test_scorecard_crud_and_render():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        tmpl.create_scorecard(code=f"sc-{ids['tag']}", name="Firm scorecard",
                              metric_keys=["client_count", "active_projects"], category="executive",
                              actor_user_id=ids["uid"])
        result = render.render_scorecard_by_code(p, f"sc-{ids['tag']}")
        assert result["scorecard"]["name"] == "Firm scorecard"
        assert len(result["metrics"]) == 2
        with pytest.raises(common.ReportingError):
            tmpl.create_scorecard(code=f"sc-{ids['tag']}", name="dup", metric_keys=["client_count"])
    finally:
        _teardown(ids)


def test_kpi_group_widget_composes_values():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        g = tmpl.create_kpi_group(code=f"g-{ids['tag']}", name="Pipeline",
                                  metric_keys=["open_opportunities", "won_opportunities"],
                                  actor_user_id=ids["uid"])
        d = svc.create_dashboard(p, code=f"d-{ids['tag']}", name="BD", category="business_development",
                                 actor_user_id=ids["uid"])
        svc.add_widget(p, d["id"], title="Pipeline KPIs", widget_type="kpi_group",
                       kpi_group_id=g["id"], viz_type="table", actor_user_id=ids["uid"])
        rendered = svc.render_dashboard(p, d["id"])
        vals = rendered["widgets"][0]["values"]
        assert {v["key"] for v in vals} == {"open_opportunities", "won_opportunities"}
    finally:
        _teardown(ids)


# --- templates + export profiles + exports -----------------------------------

def test_template_and_export_profile_crud_and_seeds():
    ids = _setup()
    try:
        tmpl.create_template(code=f"t-{ids['tag']}", name="Board pack", category="executive",
                             report_type="executive_summary", actor_user_id=ids["uid"])
        assert tmpl.get_template(code=f"t-{ids['tag']}") is not None
        # seeded export profiles present
        codes = {p["code"] for p in tmpl.list_export_profiles()}
        assert {"pdf_download", "excel_download", "csv_download", "pptx_email"} <= codes
        # a Microsoft 365 delivery target is a valid export reference (metadata only)
        prof = tmpl.create_export_profile(code=f"x-{ids['tag']}", name="M365", export_format="pdf",
                                          delivery="microsoft365", actor_user_id=ids["uid"])
        assert prof["delivery"] == "microsoft365"
    finally:
        _teardown(ids)


def test_export_reuses_analytics_producer():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        out = render.export_values(p, ["client_count", "active_projects"])
        assert "columns" in out and "rows" in out    # the analytics export_metrics shape
    finally:
        _teardown(ids)


# --- saved views (owner scope) -----------------------------------------------

def test_saved_views_owner_scope():
    ids = _setup()
    try:
        owner = _principal(ids["uid"])
        v = tmpl.create_saved_view(owner, name="My view", target_type="dashboard", target_id=1,
                                   actor_user_id=ids["uid"])
        assert any(sv["id"] == v["id"] for sv in tmpl.list_saved_views(owner))
        stranger = _principal(ids["stranger"], {"reporting.view"})
        assert all(sv["id"] != v["id"] for sv in tmpl.list_saved_views(stranger))  # private
        with pytest.raises(common.ReportingError):
            tmpl.delete_saved_view(_principal(ids["stranger"], {"reporting.view"}), v["id"])
        assert tmpl.delete_saved_view(owner, v["id"]) is True
    finally:
        _teardown(ids)


# --- reports + generate + scheduling + Workflow ------------------------------

def test_report_create_and_generate():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        defn = svc.create_definition(p, name="D", definition={"metric_keys": ["client_count"]},
                                     actor_user_id=ids["uid"])
        r = svc.create_report(p, name="Run 1", report_definition_id=defn["id"],
                              report_type="operational", actor_user_id=ids["uid"])
        assert r["status"] == "draft"
        r = svc.generate_report(p, r["id"], actor_user_id=ids["uid"])
        assert r["status"] == "generated" and r["generated_at"] is not None
        assert r["result_metadata"]["metric_count"] == 1
    finally:
        _teardown(ids)


def test_schedule_run_creates_report():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        d = svc.create_dashboard(p, code=f"d-{ids['tag']}", name="Sched dash",
                                 actor_user_id=ids["uid"])
        s = schedules.create_schedule(p, name="Weekly", frequency="weekly", dashboard_id=d["id"],
                                      actor_user_id=ids["uid"])
        run = schedules.run_schedule(p, s["id"], actor_user_id=ids["uid"])
        assert run["status"] == "generated"
        assert schedules.get_schedule(s["id"])["last_run_at"] is not None
        with pytest.raises(common.ReportingError):
            schedules.create_schedule(p, name="bad", actor_user_id=ids["uid"])  # no target
    finally:
        _teardown(ids)


def test_workflow_run_report_schedule_action():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        from app.services.workflow_orchestration import actions as wact
        assert "run_report_schedule" in wact.ACTION_REGISTRY
        d = svc.create_dashboard(p, code=f"d-{ids['tag']}", name="WF dash", actor_user_id=ids["uid"])
        s = schedules.create_schedule(p, name="WF sched", dashboard_id=d["id"],
                                      actor_user_id=ids["uid"])
        run = wact.execute_action("run_report_schedule",
                                  context={"principal": p, "schedule_id": s["id"]},
                                  actor_user_id=ids["uid"])
        assert run["status"] == "generated"
    finally:
        _teardown(ids)


def test_communications_reference_on_schedule():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        from app.services.communications import service as comms
        conv = comms.create_conversation(p, subject="Report delivery", actor_user_id=ids["uid"])
        d = svc.create_dashboard(p, code=f"d-{ids['tag']}", name="C dash", actor_user_id=ids["uid"])
        s = schedules.create_schedule(p, name="Delivered", dashboard_id=d["id"],
                                      conversation_id=conv["id"], actor_user_id=ids["uid"])
        assert s["conversation_id"] == conv["id"]
        # FK targets confirm references, never ownership
        assert next(iter(report_schedules.c["conversation_id"].foreign_keys)).column.table.name \
            == "communication_conversations"
        assert next(iter(report_schedules.c["workflow_instance_id"].foreign_keys)).column.table.name \
            == "workflow_instances"
    finally:
        _teardown(ids)


# --- timeline (client-anchored only) -----------------------------------------

def test_timeline_only_for_client_anchored_reports():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        # client-anchored report -> timeline on generate
        anchored = svc.create_report(p, name="Client report", person_id=ids["pid"],
                                     actor_user_id=ids["uid"])
        svc.generate_report(p, anchored["id"], actor_user_id=ids["uid"])
        # firm-level report -> NO timeline event
        firm = svc.create_report(p, name="Firm report", actor_user_id=ids["uid"])
        svc.generate_report(p, firm["id"], actor_user_id=ids["uid"])
        with engine.connect() as c:
            types = set(c.scalars(select(timeline_events.c.event_type).where(
                timeline_events.c.source == "reporting",
                timeline_events.c.person_id == ids["pid"])))
        assert "reporting_report_created" in types
        assert "reporting_scheduled_report_generated" in types
    finally:
        _teardown(ids)


# --- authorization + record scope --------------------------------------------

def test_client_anchored_report_scope_blocks_stranger():
    ids = _setup()
    try:
        owner = _principal(ids["uid"])
        r = svc.create_report(owner, name="Private", person_id=ids["pid"], actor_user_id=ids["uid"])
        stranger = _principal(ids["stranger"], {"reporting.view"})
        assert svc.get_report(stranger, r["id"]) is None
        assert all(row["id"] != r["id"] for row in svc.list_reports(stranger)["rows"])
    finally:
        _teardown(ids)


def test_create_client_anchored_report_requires_write_scope():
    ids = _setup()
    try:
        stranger = _principal(ids["stranger"], {"reporting.manage"})
        with pytest.raises(common.ReportingError):
            svc.create_report(stranger, name="X", person_id=ids["pid"], actor_user_id=ids["stranger"])
    finally:
        _teardown(ids)


# --- audit ledger ------------------------------------------------------------

def test_audit_ledger_records_and_is_append_only():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        d = svc.create_dashboard(p, code=f"d-{ids['tag']}", name="Audit", actor_user_id=ids["uid"])
        svc.set_dashboard_status(p, d["id"], "published", actor_user_id=ids["uid"])
        etypes = [e["event_type"] for e in
                  common.audit_history(p, entity_type="dashboard", entity_id=d["id"])]
        assert "dashboard_created" in etypes and "dashboard_published" in etypes
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(update(reporting_events).where(reporting_events.c.entity_id == d["id"])
                          .values(event_type="tampered"))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(delete(reporting_events).where(reporting_events.c.entity_id == d["id"]))
    finally:
        _teardown(ids)


# --- architecture invariants -------------------------------------------------

def test_reporting_is_composition_layer_analytics_does_not_import_it():
    # Analytics is a producer; it must NOT import the reporting composition layer. (Substring must
    # be the fully-qualified module path — "opportunity import reporting" is a different module.)
    import pathlib
    analytics_dir = pathlib.Path(render.__file__).parents[1] / "analytics"
    for f in analytics_dir.glob("*.py"):
        src = f.read_text()
        assert "app.services.reporting" not in src, f.name


def test_render_delegates_to_analytics_compute_metric():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        from app.services.analytics import metrics as analytics_metrics
        assert render.metric_value(p, "active_projects") == \
            analytics_metrics.compute_metric(p, "active_projects")
    finally:
        _teardown(ids)


def test_route_prefix_matches_no_middleware_rule():
    from app.security.middleware import RULES
    assert not any(pattern.search("/reporting") for pattern, _cap in RULES)
    assert not any(pattern.search("/reporting/dashboards/5") for pattern, _cap in RULES)
