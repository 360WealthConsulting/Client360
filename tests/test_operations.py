"""Enterprise Operations platform tests (Phase D.20).

Covers project CRUD + lifecycle, project templates (scaffolding phases/tasks), operational tasks
(firm-level and project-scoped), task dependencies (finish-to-start gate + cycle/self rejection),
checklists, capacity planning + deterministic workload/utilization + over-capacity, resource
assignment, authorization + firm-level vs client-anchored record scope, cross-domain references
(Communications live + Scheduling/Workflow/Advisor Work/Documents/Compliance/Opportunity FK
targets), the Workflow `create_operational_task` action, Timeline lifecycle events (client-anchored
only; firm-level items skip), Analytics consumption, the append-only audit ledger, and architecture
invariants. Advisor Work, the client `tasks` table, Scheduling, Communications, and the D.5 golden
are untouched.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import delete, insert, select, update

from app.db import (
    engine,
    operational_resources,
    operational_tasks,
    operations_events,
    people,
    project_templates,
    projects,
    record_assignments,
    timeline_events,
    users,
)
from app.security.models import Principal
from app.services.analytics import sources
from app.services.operations import capacity as cap
from app.services.operations import common
from app.services.operations import projects as proj
from app.services.operations import tasks as opstasks
from app.services.operations import templates as tmpl

CAPS = frozenset({"operations.view", "operations.manage", "operations.templates",
                  "operations.audit", "operations.admin", "record.read_all", "record.write_all"})


def _sfx():
    return uuid.uuid4().hex[:8]


def _principal(uid, caps=CAPS):
    return Principal(uid, "a@e.test", "A", frozenset(caps))


def _setup(*, with_org=False):
    tag = _sfx()
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"op-{tag}@e.test", normalized_email=f"op-{tag}@e.test",
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
    # operations_events is append-only (trigger-blocked) but polymorphic (no FK), so projects/tasks
    # can be deleted; the audit rows remain as leftovers.
    with engine.begin() as c:
        c.execute(delete(operational_tasks).where(operational_tasks.c.created_by_user_id == ids["uid"]))
        c.execute(delete(projects).where(projects.c.created_by_user_id == ids["uid"]))
        c.execute(delete(operational_resources).where(operational_resources.c.code.like(f"r-{ids['tag']}%")))
        c.execute(delete(project_templates).where(project_templates.c.code.like(f"t-{ids['tag']}%")))
        c.execute(delete(timeline_events).where(timeline_events.c.source == "operations",
                                                timeline_events.c.person_id == ids["pid"]))
        c.execute(delete(record_assignments).where(record_assignments.c.entity_id == ids["pid"],
                                                   record_assignments.c.entity_type == "person"))
        c.execute(delete(people).where(people.c.id == ids["pid"]))
        c.execute(delete(users).where(users.c.id.in_((ids["uid"], ids["stranger"]))))


# --- project CRUD + lifecycle ------------------------------------------------

def test_create_and_get_project():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        pr = proj.create_project(p, name="Server Migration", category="infrastructure",
                                 actor_user_id=ids["uid"])
        assert pr["id"] and pr["status"] == "planned"
        detail = proj.get_project(p, pr["id"])
        assert detail["name"] == "Server Migration"
        assert detail["tasks"] == []
    finally:
        _teardown(ids)


def test_project_lifecycle_and_invalid():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        pr = proj.create_project(p, name="Audit", category="audit", actor_user_id=ids["uid"])
        pr = proj.transition_project(p, pr["id"], "active", actor_user_id=ids["uid"])
        assert pr["status"] == "active"
        pr = proj.transition_project(p, pr["id"], "completed", actor_user_id=ids["uid"])
        assert pr["status"] == "completed" and pr["actual_end_date"] is not None
        with pytest.raises(common.OperationsError):
            proj.transition_project(p, pr["id"], "active", actor_user_id=ids["uid"])  # terminal
    finally:
        _teardown(ids)


# --- templates ---------------------------------------------------------------

def test_template_scaffolds_phases_and_tasks():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        code = f"t-{ids['tag']}"
        tmpl.create_template(code=code, name="Onboarding", category="onboarding",
                             default_phases=[{"name": "Prep", "sequence": 0},
                                             {"name": "Execute", "sequence": 1}],
                             default_tasks=[{"title": "Create accounts"}, {"title": "Order laptop"}],
                             actor_user_id=ids["uid"])
        pr = proj.create_project(p, name="Onboard Sam", template_code=code, actor_user_id=ids["uid"])
        detail = proj.get_project(p, pr["id"])
        assert len(detail["phases"]) == 2
        assert {t["title"] for t in detail["tasks"]} == {"Create accounts", "Order laptop"}
        with pytest.raises(common.OperationsError):
            tmpl.create_template(code=code, name="dup")     # duplicate code
    finally:
        _teardown(ids)


def test_seeded_project_templates_exist():
    for code in ("tax_season", "ria_audit", "server_migration", "employee_onboarding"):
        assert tmpl.get_template(code=code) is not None


# --- operational tasks + dependencies ----------------------------------------

def test_standalone_firm_task_has_no_client_anchor():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        t = opstasks.create_task(p, title="Migrate the server", actor_user_id=ids["uid"])
        assert t["person_id"] is None and t["project_id"] is None
        assert t["status"] == "planned"
    finally:
        _teardown(ids)


def test_dependency_gate_and_cycle_rejection():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        a = opstasks.create_task(p, title="A", actor_user_id=ids["uid"])
        b = opstasks.create_task(p, title="B", actor_user_id=ids["uid"])
        opstasks.add_dependency(p, b["id"], a["id"], actor_user_id=ids["uid"])  # B depends on A
        # B cannot start while A is incomplete
        with pytest.raises(common.OperationsError):
            opstasks.transition_task(p, b["id"], "active", actor_user_id=ids["uid"])
        opstasks.transition_task(p, a["id"], "active", actor_user_id=ids["uid"])
        opstasks.transition_task(p, a["id"], "completed", actor_user_id=ids["uid"])
        b2 = opstasks.transition_task(p, b["id"], "active", actor_user_id=ids["uid"])  # now allowed
        assert b2["status"] == "active"
        # cycle + self rejected
        with pytest.raises(common.OperationsError):
            opstasks.add_dependency(p, a["id"], b["id"], actor_user_id=ids["uid"])  # would cycle
        with pytest.raises(common.OperationsError):
            opstasks.add_dependency(p, a["id"], a["id"], actor_user_id=ids["uid"])  # self
    finally:
        _teardown(ids)


def test_checklist():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        t = opstasks.create_task(p, title="Setup", actor_user_id=ids["uid"])
        ci = opstasks.add_checklist_item(p, t["id"], description="Buy hardware", actor_user_id=ids["uid"])
        assert ci["done"] is False
        ci = opstasks.toggle_checklist_item(p, ci["id"], done=True, actor_user_id=ids["uid"])
        assert ci["done"] is True and ci["done_at"] is not None
    finally:
        _teardown(ids)


def test_issues_and_comments():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        pr = proj.create_project(p, name="Risky", actor_user_id=ids["uid"])
        issue = opstasks.add_issue(p, title="Vendor delay", issue_type="risk", project_id=pr["id"],
                                   severity="high", actor_user_id=ids["uid"])
        assert issue["issue_type"] == "risk"
        issue = opstasks.set_issue_status(p, issue["id"], "resolved", actor_user_id=ids["uid"])
        assert issue["status"] == "resolved" and issue["resolved_at"] is not None
        cm = opstasks.add_comment(p, body="Following up", project_id=pr["id"], actor_user_id=ids["uid"])
        assert cm["body"] == "Following up"
    finally:
        _teardown(ids)


# --- capacity / workload / utilization ---------------------------------------

def test_capacity_workload_and_utilization():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        res = tmpl.create_resource(code=f"r-{ids['tag']}", name="Ops Analyst", resource_type="staff",
                                   department="operations", capacity_minutes_per_day=480)
        # a capacity plan (persisted allocation)
        plan = cap.create_capacity_plan(resource_id=res["id"], period_start=date(2026, 8, 1),
                                        period_end=date(2026, 8, 31), planned_minutes=6000,
                                        available_minutes=9600, actor_user_id=ids["uid"])
        assert plan["planned_minutes"] == 6000
        # two open tasks assigned -> committed workload
        opstasks.create_task(p, title="T1", assigned_resource_id=res["id"], estimated_minutes=300,
                             actor_user_id=ids["uid"])
        opstasks.create_task(p, title="T2", assigned_resource_id=res["id"], estimated_minutes=300,
                             actor_user_id=ids["uid"])
        wl = cap.resource_workload(res["id"])
        assert wl["committed_minutes"] == 600 and wl["open_task_count"] == 2
        util = cap.resource_utilization(res["id"])
        assert util["available_minutes"] == 480
        assert util["over_capacity"] is True          # 600 > 480
        assert util["utilization_percent"] == 125.0
        overview = cap.capacity_overview(p)
        assert overview["over_capacity_count"] >= 1
    finally:
        _teardown(ids)


def test_assign_task_to_user_and_resource():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        res = tmpl.create_resource(code=f"r-{ids['tag']}", name="R", resource_type="staff")
        t = opstasks.create_task(p, title="Assign me", actor_user_id=ids["uid"])
        t = opstasks.assign_task(p, t["id"], assigned_user_id=ids["uid"],
                                 assigned_resource_id=res["id"], actor_user_id=ids["uid"])
        assert t["assigned_user_id"] == ids["uid"] and t["assigned_resource_id"] == res["id"]
    finally:
        _teardown(ids)


# --- authorization + record scope --------------------------------------------

def test_firm_level_items_visible_without_read_all():
    ids = _setup()
    try:
        manager = _principal(ids["uid"], {"operations.view", "operations.manage"})
        pr = proj.create_project(manager, name="Firm project", actor_user_id=ids["uid"])
        # firm-level (no client anchor) is visible to any operations.view holder
        viewer = _principal(ids["stranger"], {"operations.view"})
        assert proj.get_project(viewer, pr["id"]) is not None
    finally:
        _teardown(ids)


def test_client_anchored_project_scope_blocks_stranger():
    ids = _setup()
    try:
        owner = _principal(ids["uid"])
        pr = proj.create_project(owner, name="Client project", person_id=ids["pid"],
                                 actor_user_id=ids["uid"])
        stranger = _principal(ids["stranger"], {"operations.view", "operations.manage"})
        assert proj.get_project(stranger, pr["id"]) is None
        assert all(r["id"] != pr["id"] for r in proj.list_projects(stranger)["rows"])
    finally:
        _teardown(ids)


def test_create_client_anchored_requires_write_scope():
    ids = _setup()
    try:
        stranger = _principal(ids["stranger"], {"operations.manage"})
        with pytest.raises(common.OperationsError):
            proj.create_project(stranger, name="X", person_id=ids["pid"], actor_user_id=ids["stranger"])
    finally:
        _teardown(ids)


# --- cross-domain references + workflow action -------------------------------

def test_communications_reference_and_fk_targets():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        from app.services.communications import service as comms
        conv = comms.create_conversation(p, subject="Re: project", actor_user_id=ids["uid"])
        pr = proj.create_project(p, name="Linked", conversation_id=conv["id"], actor_user_id=ids["uid"])
        assert pr["conversation_id"] == conv["id"]

        def _target(table, col):
            return next(iter(table.c[col].foreign_keys)).column.table.name
        assert _target(projects, "opportunity_id") == "opportunities"
        assert _target(projects, "compliance_review_id") == "compliance_reviews"
        assert _target(projects, "conversation_id") == "communication_conversations"
        assert _target(projects, "workflow_instance_id") == "workflow_instances"
        assert _target(operational_tasks, "advisor_work_item_id") == "advisor_work_items"
        assert _target(operational_tasks, "meeting_id") == "meetings"   # Scheduling / M365 reference
        assert _target(operational_tasks, "document_id") == "documents"
    finally:
        _teardown(ids)


def test_workflow_create_operational_task_action():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        from app.services.workflow_orchestration import actions as wact
        assert "create_operational_task" in wact.ACTION_REGISTRY
        res = wact.execute_action("create_operational_task",
                                  context={"principal": p, "title": "WF-created task"},
                                  actor_user_id=ids["uid"])
        assert res["title"] == "WF-created task" and res["project_id"] is None
    finally:
        _teardown(ids)


# --- timeline integration ----------------------------------------------------

def test_timeline_events_client_anchored_only():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        # client-anchored project -> timeline
        pr = proj.create_project(p, name="Client TL", person_id=ids["pid"], status="active",
                                 actor_user_id=ids["uid"])
        ms = proj.add_milestone(p, pr["id"], name="Phase 1 done", actor_user_id=ids["uid"])
        proj.reach_milestone(p, ms["id"], actor_user_id=ids["uid"])
        proj.transition_project(p, pr["id"], "completed", actor_user_id=ids["uid"])
        t = opstasks.create_task(p, title="Anchored task", project_id=pr["id"], person_id=ids["pid"],
                                 actor_user_id=ids["uid"])
        opstasks.transition_task(p, t["id"], "active", actor_user_id=ids["uid"])
        opstasks.transition_task(p, t["id"], "completed", actor_user_id=ids["uid"])
        # firm-level project -> NO timeline event
        proj.create_project(p, name="Firm only", status="active", actor_user_id=ids["uid"])
        with engine.connect() as c:
            types = set(c.scalars(select(timeline_events.c.event_type).where(
                timeline_events.c.source == "operations",
                timeline_events.c.person_id == ids["pid"])))
        assert "operations_project_created" in types
        assert "operations_project_completed" in types
        assert "operations_milestone_reached" in types
        assert "operations_task_completed" in types
    finally:
        _teardown(ids)


# --- analytics consumption ---------------------------------------------------

def test_analytics_operations_metrics():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        before_proj = sources.active_project_count(p)
        before_task = sources.open_operational_task_count(p)
        proj.create_project(p, name="Metric", status="active", actor_user_id=ids["uid"])
        opstasks.create_task(p, title="Metric task", actor_user_id=ids["uid"])
        assert sources.active_project_count(p) == before_proj + 1
        assert sources.open_operational_task_count(p) == before_task + 1
        from app.services.analytics.metrics import METRICS
        assert "active_projects" in METRICS and "open_operational_tasks" in METRICS
    finally:
        _teardown(ids)


# --- audit ledger ------------------------------------------------------------

def test_audit_ledger_records_and_is_append_only():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        pr = proj.create_project(p, name="Audit", actor_user_id=ids["uid"])
        proj.transition_project(p, pr["id"], "active", actor_user_id=ids["uid"])
        etypes = [e["event_type"] for e in
                  common.audit_history(p, entity_type="project", entity_id=pr["id"])]
        assert "project_created" in etypes and "project_active" in etypes
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(update(operations_events).where(operations_events.c.project_id == pr["id"])
                          .values(event_type="tampered"))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(delete(operations_events).where(operations_events.c.project_id == pr["id"]))
    finally:
        _teardown(ids)


# --- architecture invariants -------------------------------------------------

def test_operations_does_not_import_analytics():
    import pathlib
    root = pathlib.Path(proj.__file__).parent
    for name in ("projects.py", "tasks.py", "capacity.py", "templates.py", "common.py"):
        src = (root / name).read_text()
        assert "import analytics" not in src and "services.analytics" not in src, name


def test_route_prefixes_avoid_task_and_middleware_rules():
    from app.security.middleware import RULES
    # /operations matches no rule; and operational-task routes (/operations/.../items) must NOT
    # collide with the unanchored client /tasks rule.
    assert not any(pattern.search("/operations") for pattern, _cap in RULES)
    assert not any(pattern.search("/operations/projects/5/items") for pattern, _cap in RULES)
    assert not any(pattern.search("/operations/items/5") for pattern, _cap in RULES)
