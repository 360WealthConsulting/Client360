from datetime import date, timedelta
import uuid
import pytest
from sqlalchemy import func, select

from app.db import (audit_events, documents, engine, engagement_letter_templates,
    households, people, portal_document_requests, portal_notifications, roles,
    tax_checklist_items, tax_engagement_letters, tax_missing_items, tax_organizers,
    tax_questionnaires, timeline_events, user_roles, users, workflow_steps)
from app.portal.service import PortalPrincipal, confirm_request_upload, invite_portal_account
from app.security.models import Principal
from app.services.tax_domain import create_engagement
from app.services.tax_intake import (accept_letter, intake_detail, portal_intakes,
    process_reminders, save_organizer, save_questionnaire, staff_dashboard,
    sync_documents)

def _case():
    suffix=uuid.uuid4().hex[:8]
    with engine.begin() as c:
        household_id=c.execute(households.insert().values(name=f"Intake {suffix}").returning(households.c.id)).scalar_one()
        person_id=c.execute(people.insert().values(household_id=household_id,full_name=f"Intake Client {suffix}",active=True).returning(people.c.id)).scalar_one()
        user_id=c.execute(users.insert().values(email=f"intake-{suffix}@example.com",normalized_email=f"intake-{suffix}@example.com",display_name="Intake Admin",auth_subject=f"intake-{suffix}",status="active").returning(users.c.id)).scalar_one()
        role_id=c.scalar(select(roles.c.id).where(roles.c.code=="administrator")); c.execute(user_roles.insert().values(user_id=user_id,role_id=role_id))
    account_id,_=invite_portal_account(person_id=person_id,household_id=household_id,email=f"client-{suffix}@example.com",display_name="Client",access_type="self",invited_by_user_id=user_id,permissions={"tasks":True,"documents":True,"messages":True})
    portal=PortalPrincipal(account_id,person_id,f"client-{suffix}@example.com","Client")
    result=create_engagement({"tax_year":2026,"return_type":"1040","filing_status":"single","person_id":person_id,"household_id":household_id,"assignee_user_id":user_id},actor_user_id=user_id,request_id=f"intake-{suffix}")
    return user_id,person_id,household_id,portal,result

def test_launch_creates_versioned_letter_organizer_questionnaire_checklist_notifications():
    user_id,person_id,household_id,portal,result=_case(); detail=intake_detail(result["return_id"])
    assert detail["letter"]["template_snapshot"]["version"]==1
    assert detail["organizer"]["tax_year"]==2026 and detail["organizer"]["template_snapshot"]["audience"]=="individual"
    assert len(detail["questionnaire"]["template_snapshot"]["questions"])==3
    assert len(detail["checklist"])==3 and len(detail["missing"])==2
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(portal_notifications).where(portal_notifications.c.portal_account_id==portal.account_id))==2
        assert c.scalar(select(func.count()).select_from(timeline_events).where(timeline_events.c.person_id==person_id,timeline_events.c.source=="tax_intake"))==1
        assert c.scalar(select(func.count()).select_from(audit_events).where(audit_events.c.action=="tax.intake.launched",audit_events.c.entity_id==str(result["return_id"])))==1

def test_saved_progress_conditional_required_questions_and_portal_scope():
    user_id,person_id,household_id,portal,result=_case(); return_id=result["return_id"]
    partial=save_questionnaire(return_id,{"changes":True,"foreign":False},portal_principal=portal)
    assert partial["status"]=="in_progress" and "change_details" in partial["missing_required"]
    with pytest.raises(ValueError): save_questionnaire(return_id,{},portal_principal=portal,complete=True)
    completed=save_questionnaire(return_id,{"change_details":"New business"},portal_principal=portal,complete=True)
    assert completed["status"]=="completed" and completed["progress_percent"]==100
    organizer=save_organizer(return_id,{"identity":{"confirmed":True}},portal_principal=portal)
    assert 0 < organizer["progress_percent"] < 100
    assert save_organizer(return_id,{"income":{},"deductions":{},"changes":{}},portal_principal=portal,complete=True)["status"]=="completed"
    other=PortalPrincipal(portal.account_id+99999,person_id+99999,"other@example.com","Other")
    with pytest.raises(PermissionError): save_organizer(return_id,{},portal_principal=other)

def test_acceptance_uploads_and_milestones_advance_existing_workflow():
    user_id,person_id,household_id,portal,result=_case(); return_id=result["return_id"]
    accept_letter(return_id,portal_principal=portal,metadata={"ip":"127.0.0.1"})
    save_organizer(return_id,{"identity":{},"income":{},"deductions":{},"changes":{}},portal_principal=portal,complete=True)
    save_questionnaire(return_id,{"changes":False,"foreign":False},portal_principal=portal,complete=True)
    detail=intake_detail(return_id); assert all(detail["gates"][k] for k in ("letter","organizer","questionnaire"))
    for index,item in enumerate(i for i in detail["checklist"] if i["required"]):
        with engine.begin() as c:
            unique=uuid.uuid4().hex
            document_id=c.execute(documents.insert().values(person_id=person_id,original_name=f"tax-{index}.pdf",stored_name=f"tax-{index}-{unique}.pdf",storage_path=f"/tmp/tax-{unique}.pdf",content_type="application/pdf",size_bytes=10,sha256=uuid.uuid4().hex+uuid.uuid4().hex).returning(documents.c.id)).scalar_one()
        confirm_request_upload(portal,item["portal_document_request_id"],document_id)
    detail=sync_documents(return_id); assert detail["preparer_ready"] and not detail["missing"]
    with engine.connect() as c:
        statuses={s.definition_snapshot.get("step_key"):s.status for s in c.execute(select(workflow_steps.c.definition_snapshot,workflow_steps.c.status).where(workflow_steps.c.workflow_instance_id==result["workflow_id"])).all()}
    assert statuses["intake"]=="completed" and statuses["documents"]=="completed" and statuses["prepare"]=="active"
    with engine.connect() as c: assert c.scalar(select(tax_engagement_letters.c.accepted_by_portal_account_id).where(tax_engagement_letters.c.tax_engagement_id==result["engagement_id"]))==portal.account_id

def test_dashboards_filtering_reminders_and_template_immutability():
    user_id,person_id,household_id,portal,result=_case()
    with engine.begin() as c: c.execute(tax_missing_items.update().where(tax_missing_items.c.tax_engagement_return_id==result["return_id"]).values(due_date=date.today()-timedelta(days=1)))
    assert process_reminders()>=2
    assert portal_intakes(portal)[0]["context"]["return_id"]==result["return_id"]
    admin=Principal(user_id,"admin@example.com","Admin",frozenset({"tax.intake.read","record.read_all"}))
    data=staff_dashboard(admin); assert data["metrics"]["overdue_items"]>=2 and data["metrics"]["missing_documents"]>=2
    restricted=Principal(user_id+99999,"none@example.com","None",frozenset({"tax.intake.read"}))
    assert staff_dashboard(restricted)["items"]==[]
    with pytest.raises(Exception):
        with engine.begin() as c: c.execute(engagement_letter_templates.update().where(engagement_letter_templates.c.code=="standard-tax-engagement").values(name="Mutated"))
