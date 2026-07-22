"""Enterprise Observability platform tests (Phase D.26).

Covers service CRUD + dependency graph, health checks/snapshots, diagnostic checks/results, telemetry
sources/metrics + threshold breach, alert rules/alerts/acknowledgement/resolution/suppression,
maintenance windows (activation → suppression), runtime snapshots (reusing the readiness surface),
reliability incidents (lifecycle + client-anchored timeline + record scope) and findings (referencing
Security/Integration), authorization + record scope, Automation-dispatch/Analytics/Timeline
integration, append-only audit ledger, migration seeds, and architecture invariants. The health
endpoints, scheduler snapshot, logging, exception handlers, notification dispatch, and D.5 golden are
untouched.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import delete, select, text, update

from app.db import (
    engine,
    observability_events,
    people,
    record_assignments,
    timeline_events,
    users,
)
from app.security.models import Principal
from app.services.observability import (
    alerts,
    catalog,
    health,
    incidents,
    scans,
    telemetry,
)
from app.services.observability import service as svc
from app.services.observability.common import (
    ObservabilityError,
    ObservabilityNotFound,
    audit_history,
)

CAPS = frozenset({"observability.view", "observability.manage", "observability.execute",
                  "observability.audit", "observability.admin", "record.read_all", "record.write_all"})


def _sfx():
    return uuid.uuid4().hex[:8]


def _principal(uid, caps=CAPS):
    return Principal(uid, "a@e.test", "A", frozenset(caps))


def _setup():
    tag = _sfx()
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"obs-{tag}@e.test", normalized_email=f"obs-{tag}@e.test",
            display_name=f"U {tag}", status="active").returning(users.c.id)).scalar_one()
        stranger = c.execute(users.insert().values(
            email=f"str-{tag}@e.test", normalized_email=f"str-{tag}@e.test",
            display_name=f"S {tag}", status="active").returning(users.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"Client {tag}", primary_email=f"{tag}@e.test",
            normalized_email=f"{tag}@e.test", active=True).returning(people.c.id)).scalar_one()
        c.execute(record_assignments.insert().values(
            user_id=uid, entity_type="person", entity_id=pid, assignment_type="owner",
            effective_date=date.today()))
    return {"uid": uid, "stranger": stranger, "pid": pid, "tag": tag}


def _service(ids, code=None):
    p = _principal(ids["uid"])
    return catalog.create_service(p, code=code or f"svc-{ids['tag']}", name="API",
                                  service_type="application", actor_user_id=ids["uid"])


def _teardown(ids):
    uid = ids["uid"]
    with engine.begin() as c:
        c.execute(delete(timeline_events).where(timeline_events.c.source == "observability",
                                                timeline_events.c.person_id == ids["pid"]))
        for t in ("observability_health_snapshots", "observability_diagnostic_results",
                  "observability_runtime_snapshots"):
            c.execute(text(f"DELETE FROM {t}"))
        for t in ("observability_reliability_findings", "observability_reliability_incidents",
                  "observability_alerts", "observability_alert_suppressions", "observability_alert_rules",
                  "observability_maintenance_windows", "observability_telemetry_metrics",
                  "observability_telemetry_sources", "observability_diagnostic_checks",
                  "observability_health_checks", "observability_service_dependencies",
                  "observability_deployment_references", "observability_services",
                  "observability_environment_profiles"):
            c.execute(text(f"DELETE FROM {t} WHERE created_by_user_id = :u"), {"u": uid})
        c.execute(delete(record_assignments).where(record_assignments.c.entity_id == ids["pid"],
                                                   record_assignments.c.entity_type == "person"))
        c.execute(delete(people).where(people.c.id == ids["pid"]))
        # observability_events (append-only) + audit-chain users are left as leftovers.


# --- services + dependencies -------------------------------------------------

def test_service_crud_and_status_transition():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        s = _service(ids)
        assert s["status"] == "unknown"
        degraded = catalog.set_service_status(p, s["id"], "degraded", actor_user_id=ids["uid"])
        assert degraded["status"] == "degraded"
        assert any(e["event_type"] == "service_degraded"
                   for e in audit_history(p, entity_type="service", entity_id=s["id"]))
        with pytest.raises(ObservabilityError):
            catalog.create_service(p, code=s["code"], name="dup", actor_user_id=ids["uid"])
        with pytest.raises(ObservabilityError):
            catalog.create_service(p, code=f"x-{ids['tag']}", name="x", service_type="nope",
                                   actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


def test_service_dependency_graph():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        a = _service(ids, code=f"a-{ids['tag']}")
        b = _service(ids, code=f"b-{ids['tag']}")
        dep = catalog.add_dependency(p, a["id"], b["id"], dependency_type="hard", actor_user_id=ids["uid"])
        assert dep["depends_on_service_id"] == b["id"]
        assert len(catalog.list_dependencies(service_id=a["id"])) == 1
        with pytest.raises(ObservabilityError):
            catalog.add_dependency(p, a["id"], a["id"], actor_user_id=ids["uid"])  # self-dep
        with pytest.raises(ObservabilityError):
            catalog.add_dependency(p, a["id"], b["id"], actor_user_id=ids["uid"])  # duplicate
    finally:
        _teardown(ids)


def test_environment_and_deployment_references():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        env = catalog.create_environment_profile(p, code=f"env-{ids['tag']}", name="Prod",
                                                 environment="production", actor_user_id=ids["uid"])
        dep = catalog.create_deployment_reference(p, code=f"dep-{ids['tag']}", version="0.13.0",
                                                  migration_head="x8b9c0d1e2f3",
                                                  environment_profile_id=env["id"], actor_user_id=ids["uid"])
        assert dep["version"] == "0.13.0"
        assert any(d["id"] == dep["id"] for d in catalog.list_deployment_references())
    finally:
        _teardown(ids)


# --- health checks + snapshots -----------------------------------------------

def test_health_check_and_snapshot_updates_last_status():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        s = _service(ids)
        hc = health.create_health_check(p, code=f"hc-{ids['tag']}", name="Readiness",
                                        service_id=s["id"], check_type="readiness",
                                        target_reference="/readiness", actor_user_id=ids["uid"])
        assert hc["last_status"] == "unknown"
        snap = health.record_health_snapshot(p, hc["id"], status="healthy", latency_ms=12,
                                             actor_user_id=ids["uid"])
        assert snap["status"] == "healthy"
        assert health.list_health_checks(service_id=s["id"])[0]["last_status"] == "healthy"
        assert len(health.list_health_snapshots(health_check_id=hc["id"])) == 1
        with pytest.raises(ObservabilityError):
            health.record_health_snapshot(p, hc["id"], status="bogus", actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


# --- diagnostics -------------------------------------------------------------

def test_diagnostic_check_result_and_sensitive_detail_gated():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        dc = health.create_diagnostic_check(p, code=f"dc-{ids['tag']}", name="DB probe",
                                            category="database", actor_user_id=ids["uid"])
        health.record_diagnostic_result(p, dc["id"], status="fail", summary="down",
                                        detail="sensitive connection string", actor_user_id=ids["uid"])
        # default listing strips sensitive detail
        public = health.list_diagnostic_results(diagnostic_check_id=dc["id"])
        assert public and "detail" not in public[0]
        # audit-authorized listing includes it
        priv = health.list_diagnostic_results(diagnostic_check_id=dc["id"], include_detail=True)
        assert priv[0]["detail"] == "sensitive connection string"
    finally:
        _teardown(ids)


# --- telemetry ---------------------------------------------------------------

def test_telemetry_source_metric_and_breach():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        src = telemetry.create_source(p, code=f"ts-{ids['tag']}", name="Automation",
                                      source_type="automation", reference="automation_runs",
                                      actor_user_id=ids["uid"])
        m = telemetry.create_metric(p, code=f"tm-{ids['tag']}", name="Failed jobs",
                                    telemetry_source_id=src["id"], metric_kind="gauge",
                                    warning_threshold=5, critical_threshold=10, actor_user_id=ids["uid"])
        assert m["last_value"] is None
        ok = telemetry.collect_metric(p, m["id"], 3, actor_user_id=ids["uid"])
        assert ok["breach"] is None and ok["last_value"] == 3
        warn = telemetry.collect_metric(p, m["id"], 7, actor_user_id=ids["uid"])
        assert warn["breach"] == "warning"
        crit = telemetry.collect_metric(p, m["id"], 12, actor_user_id=ids["uid"])
        assert crit["breach"] == "critical"
    finally:
        _teardown(ids)


def test_seeded_telemetry_sources_present():
    src = {s["code"] for s in telemetry.list_sources()}
    assert {"automation_runs", "outbox", "integration_sync", "scheduler"} <= src


# --- alerts + suppression + maintenance --------------------------------------

def test_alert_lifecycle_and_suppression():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        s = _service(ids)
        rule = alerts.create_rule(p, code=f"ar-{ids['tag']}", name="High failures", service_id=s["id"],
                                  severity="critical", actor_user_id=ids["uid"])
        a = alerts.raise_alert(p, code=f"al-{ids['tag']}", title="Too many failures",
                               alert_rule_id=rule["id"], service_id=s["id"], severity="critical",
                               actor_user_id=ids["uid"])
        assert a["status"] == "open"
        acked = alerts.acknowledge_alert(p, a["id"], actor_user_id=ids["uid"])
        assert acked["status"] == "acknowledged" and acked["acknowledged_by_user_id"] == ids["uid"]
        resolved = alerts.resolve_alert(p, a["id"], actor_user_id=ids["uid"])
        assert resolved["status"] == "resolved"
        # a suppression covering the service records new alerts as suppressed
        alerts.create_suppression(p, code=f"sp-{ids['tag']}", name="Silence", service_id=s["id"],
                                  actor_user_id=ids["uid"])
        a2 = alerts.raise_alert(p, code=f"al2-{ids['tag']}", title="Another", service_id=s["id"],
                                actor_user_id=ids["uid"])
        assert a2["status"] == "suppressed"
    finally:
        _teardown(ids)


def test_maintenance_window_activation_suppresses_alerts():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        s = _service(ids)
        win = alerts.create_maintenance_window(p, code=f"mw-{ids['tag']}", title="Patch",
                                               service_id=s["id"], suppress_alerts=True,
                                               actor_user_id=ids["uid"])
        assert win["status"] == "scheduled"
        alerts.set_maintenance_status(p, win["id"], "active", actor_user_id=ids["uid"])
        # active window created an active suppression → new alert on the service is suppressed
        a = alerts.raise_alert(p, code=f"mwal-{ids['tag']}", title="During maintenance",
                               service_id=s["id"], actor_user_id=ids["uid"])
        assert a["status"] == "suppressed"
        # completing the window deactivates the suppression
        alerts.set_maintenance_status(p, win["id"], "completed", actor_user_id=ids["uid"])
        a2 = alerts.raise_alert(p, code=f"mwal2-{ids['tag']}", title="After maintenance",
                                service_id=s["id"], actor_user_id=ids["uid"])
        assert a2["status"] == "open"
    finally:
        _teardown(ids)


# --- runtime snapshots -------------------------------------------------------

def test_runtime_snapshot_reuses_readiness_surface():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        snap = health.capture_runtime_snapshot(p, actor_user_id=ids["uid"])
        assert snap["database_ok"] is True and snap["migration_head"] is not None
        assert snap["migration_in_sync"] is True and snap["summary"] == "ready"
        assert health.list_runtime_snapshots()
    finally:
        _teardown(ids)


# --- reliability incidents + findings ----------------------------------------

def test_reliability_incident_lifecycle_and_client_anchor_timeline():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        s = _service(ids)
        inc = incidents.open_incident(p, code=f"ri-{ids['tag']}", title="Outage", severity="high",
                                      service_id=s["id"], person_id=ids["pid"], actor_user_id=ids["uid"])
        assert inc["status"] == "open"
        incidents.set_incident_status(p, inc["id"], "mitigated", actor_user_id=ids["uid"])
        resolved = incidents.set_incident_status(p, inc["id"], "resolved", actor_user_id=ids["uid"])
        assert resolved["resolved_at"] is not None
        with engine.connect() as c:
            types = set(c.scalars(select(timeline_events.c.event_type).where(
                timeline_events.c.source == "observability", timeline_events.c.person_id == ids["pid"])))
        assert "observability_incident_opened" in types and "observability_incident_resolved" in types
    finally:
        _teardown(ids)


def test_reliability_finding_references_security_and_integration():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        f = incidents.create_finding(p, title="Connector down", severity="high", source="security",
                                     security_finding_id=123, integration_connector_id=45,
                                     actor_user_id=ids["uid"])
        assert f["security_finding_id"] == 123 and f["integration_connector_id"] == 45
        done = incidents.set_finding_status(p, f["id"], "remediated", actor_user_id=ids["uid"])
        assert done["status"] == "remediated" and done["resolved_at"] is not None
        with pytest.raises(ObservabilityError):
            incidents.create_finding(p, title="x", source="not_a_source", actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


def test_incident_scope_blocks_stranger():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        inc = incidents.open_incident(p, code=f"sc-{ids['tag']}", title="Scoped", severity="medium",
                                      person_id=ids["pid"], actor_user_id=ids["uid"])
        stranger = _principal(ids["stranger"], caps={"observability.view"})
        with pytest.raises(ObservabilityNotFound):
            incidents.get_incident(stranger, inc["id"])
        assert all(i["id"] != inc["id"] for i in incidents.list_incidents(stranger))
    finally:
        _teardown(ids)


# --- automation / analytics / scans ------------------------------------------

def test_automation_dispatch_has_observability_scan():
    from app.services.automation import dispatch
    assert "observability_scan" in dispatch.DISPATCH_REGISTRY


def test_scan_captures_snapshot_and_raises_on_breach():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        s = _service(ids)
        m = telemetry.create_metric(p, code=f"sm-{ids['tag']}", name="Breaching",
                                    warning_threshold=1, critical_threshold=2, actor_user_id=ids["uid"])
        telemetry.collect_metric(p, m["id"], 5, actor_user_id=ids["uid"])  # over critical
        alerts.create_rule(p, code=f"sr-{ids['tag']}", name="Rule", telemetry_metric_id=m["id"],
                           service_id=s["id"], severity="critical", actor_user_id=ids["uid"])
        res = scans.run_due_scans(p, actor_user_id=ids["uid"])
        assert res["snapshot_id"] and res["rules_evaluated"] >= 1 and res["alerts_raised"] >= 1
    finally:
        _teardown(ids)


def test_analytics_consumes_observability_metrics():
    ids = _setup()
    try:
        from app.services.analytics import sources
        from app.services.analytics.metrics import METRICS
        p = _principal(ids["uid"])
        before = sources.observability_open_alert_count(p)
        alerts.raise_alert(p, code=f"an-{ids['tag']}", title="Alert", severity="warning",
                           actor_user_id=ids["uid"])
        assert sources.observability_open_alert_count(p) == before + 1
        for key in ("observability_failed_health_checks", "observability_open_alerts",
                    "observability_operational_services", "observability_diagnostic_failures",
                    "observability_reliability_incidents"):
            assert key in METRICS
    finally:
        _teardown(ids)


# --- overview facade ---------------------------------------------------------

def test_overview_metrics_aggregates():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        m = svc.overview_metrics(p)
        for k in ("operational_services", "total_services", "failed_health_checks", "open_alerts",
                  "telemetry_metrics", "reliability_incidents", "active_maintenance_windows"):
            assert k in m
    finally:
        _teardown(ids)


# --- append-only audit + architecture invariants -----------------------------

def test_audit_ledger_append_only():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        s = _service(ids)
        catalog.set_service_status(p, s["id"], "down", actor_user_id=ids["uid"])
        assert any(e["event_type"] == "service_down"
                   for e in audit_history(p, entity_type="service", entity_id=s["id"]))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(update(observability_events).where(observability_events.c.entity_id == s["id"])
                          .values(event_type="tampered"))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(delete(observability_events).where(observability_events.c.entity_id == s["id"]))
    finally:
        _teardown(ids)


def test_migration_seeds_capabilities():
    with engine.connect() as c:
        caps = set(c.scalars(text("SELECT code FROM capabilities WHERE code LIKE 'observability.%'")))
    assert {"observability.view", "observability.manage", "observability.execute",
            "observability.audit", "observability.admin"} <= caps


def test_observability_does_not_import_composition_layers():
    import pathlib
    root = pathlib.Path(svc.__file__).parent
    for name in ("service.py", "catalog.py", "health.py", "telemetry.py", "alerts.py", "incidents.py",
                 "scans.py", "common.py"):
        src = (root / name).read_text()
        for layer in ("annual_review", "business_owner", "app.services.reporting"):
            assert f"import {layer}" not in src and f"{layer} import" not in src, f"{name}:{layer}"


def test_runtime_snapshot_reuses_scheduler_status_not_a_reimplementation():
    import pathlib
    src = pathlib.Path(health.__file__).read_text()
    assert "scheduler_status" in src  # reuses the existing readiness surface


def test_route_prefix_matches_no_middleware_rule():
    from app.security.middleware import RULES
    assert not any(pattern.search("/observability") for pattern, _cap in RULES)
    assert not any(pattern.search("/observability/alerts/5") for pattern, _cap in RULES)
