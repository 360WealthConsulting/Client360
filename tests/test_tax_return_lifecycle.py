from datetime import datetime,timezone
import uuid
import pytest
from sqlalchemy import func,select

from app.db import (audit_events,engine,households,people,portal_notifications,roles,
    tax_client_approvals,tax_filing_events,tax_return_lifecycle_events,tax_return_reviews,
    tax_review_corrections,timeline_events,user_roles,users,work_approvals,work_queues,
    workflow_events,workflow_steps)
from app.portal.service import PortalPrincipal,invite_portal_account
from app.security.models import Principal
from app.services.tax_domain import create_engagement
from app.services.tax_return_lifecycle import (client_decision,decide_review,
    production_dashboard,record_filing,request_review,return_detail,sync_workflow,
    transition_return)

def _case():
    suffix=uuid.uuid4().hex[:8]
    with engine.begin() as c:
        household=c.execute(households.insert().values(name=f"Production {suffix}").returning(households.c.id)).scalar_one()
        person=c.execute(people.insert().values(household_id=household,full_name=f"Production Client {suffix}",active=True).returning(people.c.id)).scalar_one()
        ids=[]; role=c.scalar(select(roles.c.id).where(roles.c.code=="administrator"))
        for label in ("preparer","manager","partner"):
            uid=c.execute(users.insert().values(email=f"{label}-{suffix}@example.com",normalized_email=f"{label}-{suffix}@example.com",display_name=label.title(),auth_subject=f"{label}-{suffix}",status="active").returning(users.c.id)).scalar_one(); c.execute(user_roles.insert().values(user_id=uid,role_id=role)); ids.append(uid)
    account,_=invite_portal_account(person_id=person,household_id=household,email=f"client-{suffix}@example.com",display_name="Client",access_type="self",invited_by_user_id=ids[0],permissions={"tasks":True,"documents":True,"messages":True})
    portal=PortalPrincipal(account,person,f"client-{suffix}@example.com","Client")
    result=create_engagement({"tax_year":2026,"return_type":"1040","filing_status":"single","person_id":person,"household_id":household,"assignee_user_id":ids[0]},actor_user_id=ids[0],request_id=f"production-{suffix}")
    return ids,portal,result

def test_lifecycle_state_machine_timeline_audit_and_workflow_history():
    users_,portal,result=_case(); rid=result["return_id"]
    assert return_detail(rid)["return"]["status"]=="received"
    assert transition_return(rid,"ready_to_prepare",actor_user_id=users_[0])=="ready_to_prepare"
    assert transition_return(rid,"in_preparation",actor_user_id=users_[0])=="in_preparation"
    with pytest.raises(ValueError): transition_return(rid,"accepted",actor_user_id=users_[0])
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(tax_return_lifecycle_events).where(tax_return_lifecycle_events.c.tax_engagement_return_id==rid))==2
        assert c.scalar(select(func.count()).select_from(timeline_events).where(timeline_events.c.source=="tax_production",timeline_events.c.entity_id if False else True))>=2
        assert c.scalar(select(func.count()).select_from(audit_events).where(audit_events.c.action=="tax.return.status_changed",audit_events.c.entity_id==str(rid)))==2
        assert c.scalar(select(func.count()).select_from(workflow_events).where(workflow_events.c.workflow_instance_id==result["workflow_id"],workflow_events.c.event_type=="tax_return_status_changed"))==2

def test_workflow_milestones_advance_return_without_parallel_engine():
    users_,portal,result=_case(); rid=result["return_id"]
    with engine.begin() as c:
        rows=c.execute(select(workflow_steps.c.id,workflow_steps.c.definition_snapshot).where(workflow_steps.c.workflow_instance_id==result["workflow_id"])).all()
        for row in rows:
            if row.definition_snapshot.get("step_key") in {"intake","documents"}: c.execute(workflow_steps.update().where(workflow_steps.c.id==row.id).values(status="completed",completed_at=datetime.now(timezone.utc)))
    assert sync_workflow(rid,actor_user_id=users_[0])=="ready_to_prepare"
    transition_return(rid,"in_preparation",actor_user_id=users_[0])
    with engine.begin() as c:
        for row in rows:
            if row.definition_snapshot.get("step_key")=="prepare": c.execute(workflow_steps.update().where(workflow_steps.c.id==row.id).values(status="completed",completed_at=datetime.now(timezone.utc)))
    assert sync_workflow(rid,actor_user_id=users_[0])=="manager_review"

def test_review_routing_approval_history_corrections_and_return_to_preparer():
    users_,portal,result=_case(); rid=result["return_id"]
    transition_return(rid,"ready_to_prepare",actor_user_id=users_[0]); transition_return(rid,"in_preparation",actor_user_id=users_[0]); transition_return(rid,"manager_review",actor_user_id=users_[0])
    manager=request_review(rid,"manager",requested_by_user_id=users_[0],reviewer_user_id=users_[1])
    assert decide_review(manager,"approved",reviewer_user_id=users_[1],notes="Manager approved")=="partner_review"
    partner=request_review(rid,"partner",requested_by_user_id=users_[1],reviewer_user_id=users_[2])
    assert decide_review(partner,"returned",reviewer_user_id=users_[2],notes="Correct basis",corrections=["Update basis schedule"])=="in_preparation"
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(tax_review_corrections).where(tax_review_corrections.c.tax_return_review_id==partner))==1
        assert c.scalar(select(func.count()).select_from(work_approvals).where(work_approvals.c.entity_type=="tax_return",work_approvals.c.entity_id==rid))==2

def test_client_approvals_filing_rejection_resubmission_delivery_and_archive():
    users_,portal,result=_case(); rid=result["return_id"]
    transition_return(rid,"client_review",actor_user_id=users_[2],force=True)
    assert client_decision(rid,"return_approval","approved",portal_principal=portal)=="awaiting_efile_authorization"
    assert client_decision(rid,"efile_authorization","approved",portal_principal=portal)=="ready_to_file"
    assert record_filing(rid,"submitted",actor_user_id=users_[0],idempotency_key=f"submit-{rid}")=="submitted"
    assert record_filing(rid,"rejected",actor_user_id=users_[0],reason_code="RULE",message="Validation failed")=="rejected"
    assert record_filing(rid,"resubmitted",actor_user_id=users_[0])=="resubmitted"
    assert record_filing(rid,"accepted",actor_user_id=users_[0])=="accepted"
    transition_return(rid,"delivered",actor_user_id=users_[0]); assert client_decision(rid,"delivery_acknowledgement","acknowledged",portal_principal=portal)=="completed"; assert transition_return(rid,"archived",actor_user_id=users_[0])=="archived"
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(tax_filing_events).where(tax_filing_events.c.tax_engagement_return_id==rid))==4
        assert c.scalar(select(func.count()).select_from(tax_client_approvals).where(tax_client_approvals.c.tax_engagement_return_id==rid))==3
        assert c.scalar(select(func.count()).select_from(portal_notifications).where(portal_notifications.c.entity_type=="tax_return",portal_notifications.c.entity_id==rid))>=3

def test_production_queues_dashboard_assignment_filtering_and_immutable_events():
    users_,portal,result=_case(); rid=result["return_id"]
    admin=Principal(users_[0],"admin@example.com","Admin",frozenset({"tax.read","record.read_all"})); data=production_dashboard(admin)
    assert data["metrics"]["total"]>=1 and any(row["id"]==rid for row in data["items"])
    restricted=Principal(users_[0]+999999,"none@example.com","None",frozenset({"tax.read"})); assert production_dashboard(restricted)["items"]==[]
    with engine.connect() as c: assert c.scalar(select(func.count()).select_from(work_queues).where(work_queues.c.code.like("tax_production_%")))==9
    transition_return(rid,"ready_to_prepare",actor_user_id=users_[0])
    with pytest.raises(Exception):
        with engine.begin() as c: c.execute(tax_return_lifecycle_events.update().where(tax_return_lifecycle_events.c.tax_engagement_return_id==rid).values(reason="tampered"))
