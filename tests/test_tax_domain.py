from datetime import date
import uuid
from sqlalchemy import func, select

from app.db import (audit_events, engine, households, people, roles, tax_deadlines,
    tax_engagement_returns, tax_engagements, tax_workflow_links, timeline_events,
    user_roles, users, workflow_instances)
from app.security.models import Principal
from app.services.tax_domain import business_due_date, create_engagement, dashboard, reference_data

def _actor_and_client():
    suffix = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        household_id = c.execute(households.insert().values(name=f"Tax {suffix}").returning(households.c.id)).scalar_one()
        person_id = c.execute(people.insert().values(household_id=household_id, full_name=f"Tax Client {suffix}", active=True).returning(people.c.id)).scalar_one()
        user_id = c.execute(users.insert().values(email=f"tax-{suffix}@example.com", normalized_email=f"tax-{suffix}@example.com", display_name="Tax Admin", auth_subject=f"tax-{suffix}", status="active").returning(users.c.id)).scalar_one()
        role_id = c.scalar(select(roles.c.id).where(roles.c.code == "administrator"))
        c.execute(user_roles.insert().values(user_id=user_id, role_id=role_id))
    return user_id, household_id, person_id

def test_deadline_weekend_and_holiday_adjustment():
    assert business_due_date(2027, 4, 17) == date(2027, 4, 19)
    assert business_due_date(2027, 4, 16, [date(2027, 4, 16)]) == date(2027, 4, 19)

def test_reference_data_and_seeded_work_queues():
    data = reference_data()
    assert {r["code"] for r in data["return_types"]} >= {"1040", "1065", "1120S", "1041", "990"}
    assert data["firms"][0]["code"] == "360-tax"

def test_engagement_launch_reuses_workflow_timeline_audit_and_assignment():
    user_id, household_id, person_id = _actor_and_client()
    request_id = f"tax-{uuid.uuid4()}"
    result = create_engagement({"tax_year": 2026, "return_type":"1040", "filing_status":"single", "person_id":person_id,
        "household_id":household_id, "assignee_user_id":user_id, "priority":"high"}, actor_user_id=user_id, request_id=request_id)
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(tax_engagements).where(tax_engagements.c.id == result["engagement_id"])) == 1
        assert c.scalar(select(func.count()).select_from(tax_deadlines).where(tax_deadlines.c.tax_engagement_return_id == result["return_id"])) == 1
        assert c.scalar(select(func.count()).select_from(tax_workflow_links).where(tax_workflow_links.c.workflow_instance_id == result["workflow_id"])) == 1
        assert c.scalar(select(workflow_instances.c.template_snapshot["code"].astext).where(workflow_instances.c.id == result["workflow_id"])) == "tax_engagement_foundation"
        assert c.scalar(select(func.count()).select_from(timeline_events).where(timeline_events.c.person_id == person_id, timeline_events.c.source == "tax_domain")) == 1
        assert c.scalar(select(func.count()).select_from(audit_events).where(audit_events.c.action == "tax.engagement.created", audit_events.c.request_id == request_id)) == 1
    principal = Principal(user_id, "tax@example.com", "Tax Admin", frozenset({"tax.read", "record.read_all"}))
    data = dashboard(principal, tax_year=2026)
    assert data["metrics"]["returns"] >= 1 and any(i["return_id"] == result["return_id"] for i in data["items"])

def test_record_filtering_excludes_unassigned_tax_returns():
    user_id, household_id, person_id = _actor_and_client()
    create_engagement({"tax_year": 2025, "return_type":"1040", "person_id":person_id, "household_id":household_id}, actor_user_id=user_id, request_id=f"tax-{uuid.uuid4()}")
    restricted = Principal(user_id + 999999, "none@example.com", "None", frozenset({"tax.read"}))
    assert dashboard(restricted, tax_year=2025)["items"] == []
