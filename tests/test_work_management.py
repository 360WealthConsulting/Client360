from datetime import date, datetime, timedelta, timezone
import uuid
from sqlalchemy import func, select
import pytest

from app.db import (
    assignment_events, assignment_rules, audit_events, engine, households, people, record_assignments,
    roles, tasks, team_memberships, teams, timeline_events, user_roles, users,
)
from app.security.models import Principal
from app.services.work_intelligence import (
    bottlenecks, capacity_metrics, daily_agenda, priority_score, queue_matches, sla_risk,
)
from app.services.work_management import apply_assignment_rules, assign_work, dashboard, deactivate_assignment, reassign_work
from app.main import app


def test_priority_capacity_queue_sla_and_bottleneck_calculations_are_explainable():
    now = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
    urgent = {"title": "Urgent", "priority": "urgent", "status": "open", "due_date": date(2026, 7, 13), "sla_due_at": now + timedelta(hours=4), "estimated_minutes": 300, "waiting_on": "client"}
    normal = {"title": "Normal", "priority": "normal", "status": "open", "due_date": date(2026, 7, 20), "estimated_minutes": 240}
    assert priority_score(urgent, now) > priority_score(normal, now)
    assert sla_risk(urgent, now)["level"] == "critical"
    assert capacity_metrics([urgent, normal])["over_capacity"] is True
    assert queue_matches(urgent, {"overdue": True}, today=now.date())
    assert queue_matches(urgent, {"waiting_on": "client"})
    assert daily_agenda([normal, urgent], now)[0]["title"] == "Urgent"
    assert bottlenecks([urgent])[0]["reason"] == "client"


def _seed():
    suffix = uuid.uuid4().hex[:10]
    with engine.begin() as connection:
        household_id = connection.execute(households.insert().values(name=f"Work {suffix}").returning(households.c.id)).scalar_one()
        person_id = connection.execute(people.insert().values(household_id=household_id, full_name=f"Work Person {suffix}", active=True).returning(people.c.id)).scalar_one()
        task_id = connection.execute(tasks.insert().values(person_id=person_id, household_id=household_id, title="Work validation", priority="high", status="open", estimated_minutes=60, due_date=date.today()).returning(tasks.c.id)).scalar_one()
        advisor_role = connection.scalar(select(roles.c.id).where(roles.c.code == "advisor"))
        users_created = []
        for label in ("one", "two"):
            user_id = connection.execute(users.insert().values(email=f"{label}-{suffix}@example.com", normalized_email=f"{label}-{suffix}@example.com", display_name=label.title(), auth_subject=f"{label}-{suffix}", status="active").returning(users.c.id)).scalar_one()
            connection.execute(user_roles.insert().values(user_id=user_id, role_id=advisor_role))
            users_created.append(user_id)
        team_id = connection.scalar(select(teams.c.id).where(teams.c.code == "operations"))
        connection.execute(team_memberships.insert().values(user_id=users_created[0], team_id=team_id))
    return household_id, person_id, task_id, users_created, team_id


def test_assignment_reassignment_team_scope_history_timeline_and_audit():
    household_id, person_id, task_id, user_ids, team_id = _seed()
    assignment_id = assign_work(entity_type="task", entity_id=task_id, assignment_role="primary", user_id=user_ids[0], actor_user_id=user_ids[0], reason="Initial owner", request_id="work-test-create")
    secondary_id = assign_work(entity_type="task", entity_id=task_id, assignment_role="secondary", team_id=team_id, actor_user_id=user_ids[0], reason="Operations support", request_id="work-test-team")
    new_id = reassign_work(assignment_id, user_id=user_ids[1], actor_user_id=user_ids[0], reason="Capacity balance", request_id="work-test-reassign")
    principal = Principal(user_ids[0], "one@example.com", "One", frozenset({"work.read"}))
    data = dashboard(principal)
    assert any(item["entity_id"] == task_id for item in data["items"])
    unauthorized = Principal(999999, "none@example.com", "None", frozenset({"work.read"}))
    assert not any(item["entity_id"] == task_id for item in dashboard(unauthorized)["items"])
    firm_wide = Principal(999998, "admin@example.com", "Admin", frozenset({"work.read", "record.read_all"}))
    assert any(item["entity_id"] == task_id for item in dashboard(firm_wide)["items"])
    with engine.connect() as connection:
        old = connection.execute(select(record_assignments).where(record_assignments.c.id == assignment_id)).mappings().one()
        new = connection.execute(select(record_assignments).where(record_assignments.c.id == new_id)).mappings().one()
        events = connection.scalar(select(func.count()).select_from(assignment_events).where(assignment_events.c.entity_type == "task", assignment_events.c.entity_id == task_id))
        audits = connection.scalar(select(func.count()).select_from(audit_events).where(audit_events.c.entity_type == "task", audit_events.c.entity_id == str(task_id)))
        timeline = connection.scalar(select(func.count()).select_from(timeline_events).where(timeline_events.c.person_id == person_id, timeline_events.c.source == "work_management"))
    assert old["inactive_date"] is not None
    assert new["user_id"] == user_ids[1]
    assert events == 3 and audits == 3 and timeline == 3
    deactivate_assignment(secondary_id, actor_user_id=user_ids[0], reason="Complete", request_id="work-test-remove")


def test_assignment_history_is_database_immutable():
    _, _, task_id, user_ids, _ = _seed()
    assignment_id = assign_work(entity_type="task", entity_id=task_id, assignment_role="primary", user_id=user_ids[0], actor_user_id=user_ids[0], request_id="work-test-immutable")
    with pytest.raises(Exception):
        with engine.begin() as connection:
            event_id = connection.scalar(select(assignment_events.c.id).where(assignment_events.c.assignment_id == assignment_id))
            connection.execute(assignment_events.update().where(assignment_events.c.id == event_id).values(reason="tamper"))


def test_automatic_assignment_rules_are_deterministic():
    _, _, task_id, user_ids, team_id = _seed()
    with engine.begin() as connection:
        connection.execute(assignment_rules.insert().values(
            name=f"Tax work {uuid.uuid4().hex}", entity_type="task",
            conditions={"work_type": "tax"}, assignment_role="primary",
            assignee_team_id=team_id, priority=10,
        ))
    assert apply_assignment_rules("task", task_id, {"work_type": "general"}, user_ids[0], "rule-no-match") == []
    created = apply_assignment_rules("task", task_id, {"work_type": "tax"}, user_ids[0], "rule-match")
    assert len(created) == 1
    with engine.connect() as connection:
        row = connection.execute(select(record_assignments).where(record_assignments.c.id == created[0])).mappings().one()
    assert row["team_id"] == team_id and row["assignment_type"] == "primary"


def test_versioned_work_api_contracts_are_registered():
    routes = {(route.path, method) for route in app.routes for method in (getattr(route, "methods", None) or set())}
    expected = {
        ("/api/v1/work/my-work", "GET"), ("/api/v1/work/team-work", "GET"),
        ("/api/v1/work/queues", "GET"), ("/api/v1/work/queues/{code}", "GET"),
        ("/api/v1/work/capacity", "GET"), ("/api/v1/work/assignments", "GET"),
        ("/api/v1/work/assignments", "POST"),
        ("/api/v1/work/assignments/{assignment_id}/reassign", "POST"),
        ("/api/v1/work/dashboard-metrics", "GET"),
        ("/api/v1/work/daily-agenda", "GET"),
    }
    assert expected <= routes
