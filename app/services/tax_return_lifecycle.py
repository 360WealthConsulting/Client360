from datetime import date, datetime, timezone
import uuid
from sqlalchemy import case, func, or_, select

from app.db import (engine, portal_accounts, record_assignments, tax_client_approvals,
    tax_engagement_returns, tax_engagements, tax_filing_events, tax_return_lifecycle_events,
    tax_return_reviews, tax_review_corrections, tax_return_types, tax_workflow_links,
    work_approvals, workflow_events, workflow_instances, workflow_steps)
from app.portal.service import notify, portal_scope, require_scope
from app.security.audit import write_audit_event
from app.services.tax_domain import list_engagements
from app.services.timeline import add_timeline_event

STATES=("received","ready_to_prepare","in_preparation","awaiting_information","manager_review","partner_review","client_review","awaiting_efile_authorization","ready_to_file","filed","accepted","rejected","delivered","completed","archived")
TRANSITIONS={
    "received":{"ready_to_prepare","awaiting_information"},"ready_to_prepare":{"in_preparation","awaiting_information"},
    "in_preparation":{"awaiting_information","manager_review"},"awaiting_information":{"ready_to_prepare","in_preparation"},
    "manager_review":{"in_preparation","partner_review"},"partner_review":{"in_preparation","client_review"},
    "client_review":{"in_preparation","awaiting_efile_authorization"},"awaiting_efile_authorization":{"ready_to_file"},
    "ready_to_file":{"filed"},"filed":{"accepted","rejected"},"rejected":{"ready_to_file","filed"},
    "accepted":{"delivered"},"delivered":{"completed"},"completed":{"archived"},"archived":set(),
}
FILING_TRANSITIONS={"ready":{"submitted"},"submitted":{"accepted","rejected"},"rejected":{"resubmitted"},"resubmitted":{"accepted","rejected"},"accepted":set()}

def _context(c,return_id):
    return c.execute(select(tax_engagement_returns,tax_engagements.c.person_id,tax_engagements.c.household_id,tax_return_types.c.code.label("return_type"),tax_workflow_links.c.workflow_instance_id)
        .select_from(tax_engagement_returns.join(tax_engagements).join(tax_return_types).outerjoin(tax_workflow_links,tax_workflow_links.c.tax_engagement_return_id==tax_engagement_returns.c.id))
        .where(tax_engagement_returns.c.id==return_id)).mappings().one_or_none()

def _publish(context,from_status,to_status,reason,actor_user_id,portal_account_id,request_id):
    add_timeline_event(person_id=context["person_id"],household_id=context["household_id"],source="tax_production",event_type="tax_return_status_changed",title=f"Tax return moved to {to_status.replace('_',' ')}",external_id=f"tax-return-{context['id']}-{to_status}-{uuid.uuid4().hex}",event_metadata={"from":from_status,"to":to_status,"reason":reason})
    write_audit_event(action="tax.return.status_changed",entity_type="tax_return",entity_id=context["id"],actor_user_id=actor_user_id,request_id=request_id or f"tax-return-{uuid.uuid4()}",metadata={"from":from_status,"to":to_status,"reason":reason,"portal_account_id":portal_account_id})

def transition_return(return_id,to_status,*,actor_user_id=None,portal_account_id=None,reason=None,request_id=None,force=False):
    if to_status not in STATES: raise ValueError("Unsupported lifecycle status")
    now=datetime.now(timezone.utc)
    with engine.begin() as c:
        context=_context(c,return_id)
        if not context: raise ValueError("Tax return not found")
        current=context["status"]
        if current==to_status: return to_status
        if not force and to_status not in TRANSITIONS.get(current,set()): raise ValueError(f"Cannot transition from {current} to {to_status}")
        values={"status":to_status,"status_entered_at":now}
        if to_status=="in_preparation" and not context["preparation_started_at"]: values["preparation_started_at"]=now
        if to_status=="manager_review": values["preparation_completed_at"]=now
        if to_status=="filed": values["filed_at"]=now
        if to_status=="accepted": values["accepted_at"]=now
        if to_status=="delivered": values["delivered_at"]=now
        if to_status=="archived": values["archived_at"]=now
        c.execute(tax_engagement_returns.update().where(tax_engagement_returns.c.id==return_id).values(**values))
        c.execute(tax_return_lifecycle_events.insert().values(tax_engagement_return_id=return_id,from_status=current,to_status=to_status,reason=reason,actor_user_id=actor_user_id,portal_account_id=portal_account_id))
        if context["workflow_instance_id"]:
            c.execute(workflow_events.insert().values(workflow_instance_id=context["workflow_instance_id"],event_type="tax_return_status_changed",idempotency_key=f"tax-status:{return_id}:{to_status}:{uuid.uuid4().hex}",actor_user_id=actor_user_id,payload={"from":current,"to":to_status,"reason":reason}))
        if to_status in {"client_review","awaiting_efile_authorization","delivered"}:
            approval_type={"client_review":"return_approval","awaiting_efile_authorization":"efile_authorization","delivered":"delivery_acknowledgement"}[to_status]
            exists=c.scalar(select(tax_client_approvals.c.id).where(tax_client_approvals.c.tax_engagement_return_id==return_id,tax_client_approvals.c.approval_type==approval_type))
            if not exists: c.execute(tax_client_approvals.insert().values(tax_engagement_return_id=return_id,approval_type=approval_type))
        accounts=list(c.scalars(select(portal_accounts.c.id).where(portal_accounts.c.person_id==context["person_id"],portal_accounts.c.status.in_(("active","invited")))))
    if to_status in {"client_review","awaiting_efile_authorization","delivered","rejected"}:
        for account in accounts: notify(account,f"tax_return_{to_status}",f"Tax return: {to_status.replace('_',' ')}",entity_type="tax_return",entity_id=return_id,idempotency_key=f"tax-return:{return_id}:{to_status}:{account}")
    _publish(context,current,to_status,reason,actor_user_id,portal_account_id,request_id)
    return to_status

def request_review(return_id,review_type,*,requested_by_user_id,reviewer_user_id=None,reviewer_team_id=None,due_at=None):
    if review_type not in {"preparer","manager","partner"}: raise ValueError("Unsupported review type")
    if not reviewer_user_id and not reviewer_team_id: raise ValueError("Reviewer is required")
    with engine.begin() as c:
        existing=c.scalar(select(tax_return_reviews.c.id).where(tax_return_reviews.c.tax_engagement_return_id==return_id,tax_return_reviews.c.review_type==review_type))
        if existing: return existing
        approval_id=c.execute(work_approvals.insert().values(entity_type="tax_return",entity_id=return_id,approval_type=f"tax_{review_type}_review",requested_by_user_id=requested_by_user_id,approver_user_id=reviewer_user_id,approver_team_id=reviewer_team_id,due_at=due_at,requires_independent_approver=True).returning(work_approvals.c.id)).scalar_one()
        return c.execute(tax_return_reviews.insert().values(tax_engagement_return_id=return_id,review_type=review_type,reviewer_user_id=reviewer_user_id,reviewer_team_id=reviewer_team_id,work_approval_id=approval_id).returning(tax_return_reviews.c.id)).scalar_one()

def decide_review(review_id,decision,*,reviewer_user_id,notes=None,corrections=None,request_id=None):
    if decision not in {"approved","returned"}: raise ValueError("Unsupported review decision")
    now=datetime.now(timezone.utc)
    with engine.begin() as c:
        review=c.execute(select(tax_return_reviews).where(tax_return_reviews.c.id==review_id).with_for_update()).mappings().one_or_none()
        if not review or review["status"]!="pending": raise ValueError("Pending review not found")
        if review["reviewer_user_id"] and review["reviewer_user_id"]!=reviewer_user_id: raise PermissionError("Review assigned to another user")
        c.execute(tax_return_reviews.update().where(tax_return_reviews.c.id==review_id).values(status=decision,notes=notes,completed_at=now if decision=="approved" else None,returned_at=now if decision=="returned" else None))
        c.execute(work_approvals.update().where(work_approvals.c.id==review["work_approval_id"]).values(status="approved" if decision=="approved" else "rejected",approver_user_id=reviewer_user_id,decided_at=now,decision_notes=notes))
        for text in corrections or []: c.execute(tax_review_corrections.insert().values(tax_return_review_id=review_id,description=text,created_by_user_id=reviewer_user_id))
        return_id=review["tax_engagement_return_id"]; review_type=review["review_type"]
    target="in_preparation" if decision=="returned" else {"preparer":"manager_review","manager":"partner_review","partner":"client_review"}[review_type]
    return transition_return(return_id,target,actor_user_id=reviewer_user_id,reason=notes or f"{review_type} review {decision}",request_id=request_id)

def review_return_id(review_id):
    with engine.connect() as c:
        return c.scalar(select(tax_return_reviews.c.tax_engagement_return_id).where(tax_return_reviews.c.id==review_id))

def correction_return_id(correction_id):
    with engine.connect() as c:
        return c.scalar(select(tax_return_reviews.c.tax_engagement_return_id)
            .select_from(tax_review_corrections.join(tax_return_reviews,tax_return_reviews.c.id==tax_review_corrections.c.tax_return_review_id))
            .where(tax_review_corrections.c.id==correction_id))

def resolve_correction(correction_id,*,actor_user_id):
    with engine.begin() as c:
        changed=c.execute(tax_review_corrections.update().where(tax_review_corrections.c.id==correction_id,tax_review_corrections.c.status=="open").values(status="resolved",resolved_by_user_id=actor_user_id,resolved_at=datetime.now(timezone.utc))).rowcount
    if not changed: raise ValueError("Open correction not found")

def client_decision(return_id,approval_type,decision,*,portal_principal,notes=None,request_id=None):
    if approval_type not in {"return_approval","efile_authorization","delivery_acknowledgement"} or decision not in {"approved","rejected","acknowledged"}: raise ValueError("Unsupported client decision")
    with engine.begin() as c:
        context=_context(c,return_id)
        if not context: raise ValueError("Tax return not found")
        require_scope(portal_principal,person_id=context["person_id"],household_id=context["household_id"],permission="tasks")
        row=c.execute(select(tax_client_approvals).where(tax_client_approvals.c.tax_engagement_return_id==return_id,tax_client_approvals.c.approval_type==approval_type).with_for_update()).mappings().one_or_none()
        if not row: raise ValueError("Client approval not requested")
        if row["status"]!="pending": return row["status"]
        c.execute(tax_client_approvals.update().where(tax_client_approvals.c.id==row["id"]).values(status=decision,portal_account_id=portal_principal.account_id,decision_notes=notes,decided_at=datetime.now(timezone.utc)))
    if decision=="rejected": target="in_preparation"
    else: target={"return_approval":"awaiting_efile_authorization","efile_authorization":"ready_to_file","delivery_acknowledgement":"completed"}[approval_type]
    return transition_return(return_id,target,portal_account_id=portal_principal.account_id,reason=notes or decision,request_id=request_id)

def record_filing(return_id,filing_status,*,provider_key="manual",external_id=None,submission_id=None,reason_code=None,message=None,actor_user_id=None,idempotency_key=None,metadata=None,request_id=None):
    if filing_status not in FILING_TRANSITIONS and filing_status not in {"resubmitted"}: raise ValueError("Unsupported filing status")
    with engine.begin() as c:
        context=_context(c,return_id)
        if not context: raise ValueError("Tax return not found")
        current=context["filing_status"]
        if current==filing_status: return filing_status
        if filing_status not in FILING_TRANSITIONS.get(current,set()): raise ValueError(f"Cannot transition filing from {current} to {filing_status}")
        key=idempotency_key or f"filing:{return_id}:{filing_status}:{uuid.uuid4().hex}"
        existing=c.scalar(select(tax_filing_events.c.id).where(tax_filing_events.c.idempotency_key==key))
        if existing: return filing_status
        c.execute(tax_filing_events.insert().values(tax_engagement_return_id=return_id,filing_status=filing_status,provider_key=provider_key,external_id=external_id,submission_id=submission_id,reason_code=reason_code,message=message,actor_user_id=actor_user_id,idempotency_key=key,metadata=metadata or {}))
        c.execute(tax_engagement_returns.update().where(tax_engagement_returns.c.id==return_id).values(filing_status=filing_status,filing_provider_key=provider_key,filing_external_id=external_id))
    target={"submitted":"filed","accepted":"accepted","rejected":"rejected","resubmitted":"filed"}.get(filing_status)
    if target: transition_return(return_id,target,actor_user_id=actor_user_id,reason=message or f"Filing {filing_status}",request_id=request_id)
    return filing_status

def sync_workflow(return_id,*,actor_user_id=None):
    with engine.connect() as c:
        context=_context(c,return_id)
        if not context or not context["workflow_instance_id"]: return None
        steps=c.execute(select(workflow_steps).where(workflow_steps.c.workflow_instance_id==context["workflow_instance_id"])).mappings().all()
    completed={(s["definition_snapshot"] or {}).get("step_key") for s in steps if s["status"]=="completed"}
    current=context["status"]
    target=None
    if {"intake","documents"}<=completed and current=="received": target="ready_to_prepare"
    if "prepare" in completed and current=="in_preparation": target="manager_review"
    if "review" in completed and current in {"manager_review","partner_review"}: target="client_review"
    if "file" in completed and current=="ready_to_file": target="filed"
    return transition_return(return_id,target,actor_user_id=actor_user_id,reason="Workflow milestone completed") if target else current

def return_detail(return_id):
    with engine.connect() as c:
        context=_context(c,return_id)
        if not context: raise ValueError("Tax return not found")
        events=c.execute(select(tax_return_lifecycle_events).where(tax_return_lifecycle_events.c.tax_engagement_return_id==return_id).order_by(tax_return_lifecycle_events.c.created_at)).mappings().all()
        reviews=c.execute(select(tax_return_reviews).where(tax_return_reviews.c.tax_engagement_return_id==return_id).order_by(tax_return_reviews.c.requested_at)).mappings().all()
        approvals=c.execute(select(tax_client_approvals).where(tax_client_approvals.c.tax_engagement_return_id==return_id)).mappings().all()
        filings=c.execute(select(tax_filing_events).where(tax_filing_events.c.tax_engagement_return_id==return_id).order_by(tax_filing_events.c.created_at)).mappings().all()
    return {"return":dict(context),"events":[dict(x) for x in events],"reviews":[dict(x) for x in reviews],"client_approvals":[dict(x) for x in approvals],"filing_events":[dict(x) for x in filings]}

def production_dashboard(principal):
    authorized=list_engagements(principal); ids=[r["return_id"] for r in authorized]
    if not ids: return {"items":[],"metrics":{"total":0,"overdue":0,"awaiting_client":0,"awaiting_filing":0,"average_preparation_hours":0,"velocity_30_days":0},"by_status":{},"filing":{}}
    with engine.connect() as c:
        rows=c.execute(select(tax_engagement_returns.c.id,tax_engagement_returns.c.status,tax_engagement_returns.c.priority,tax_engagement_returns.c.preparation_started_at,tax_engagement_returns.c.preparation_completed_at,tax_engagement_returns.c.status_entered_at,tax_engagement_returns.c.filing_status,record_assignments.c.user_id.label("assignee_user_id")).outerjoin(record_assignments, (record_assignments.c.entity_type=="tax_return")&(record_assignments.c.entity_id==tax_engagement_returns.c.id)&(record_assignments.c.inactive_date.is_(None))).where(tax_engagement_returns.c.id.in_(ids))).mappings().all()
        review_rows=c.execute(select(tax_return_reviews.c.reviewer_user_id,tax_return_reviews.c.status).where(tax_return_reviews.c.tax_engagement_return_id.in_(ids))).all()
    now=datetime.now(timezone.utc); by_status={s:sum(r["status"]==s for r in rows) for s in STATES}; durations=[(r["preparation_completed_at"]-r["preparation_started_at"]).total_seconds()/3600 for r in rows if r["preparation_started_at"] and r["preparation_completed_at"]]
    metrics={"total":len(rows),"overdue":sum((now-r["status_entered_at"]).days>7 and r["status"] not in {"completed","archived"} for r in rows),"awaiting_client":sum(r["status"] in {"awaiting_information","client_review","awaiting_efile_authorization"} for r in rows),"awaiting_filing":sum(r["status"] in {"ready_to_file","filed","rejected"} for r in rows),"average_preparation_hours":round(sum(durations)/len(durations),2) if durations else 0,"velocity_30_days":sum(r["status"] in {"delivered","completed","archived"} and (now-r["status_entered_at"]).days<=30 for r in rows)}
    preparers={str(uid):sum(r["assignee_user_id"]==uid for r in rows) for uid in {r["assignee_user_id"] for r in rows if r["assignee_user_id"]}}
    reviewers={str(uid):sum(r.reviewer_user_id==uid and r.status=="pending" for r in review_rows) for uid in {r.reviewer_user_id for r in review_rows if r.reviewer_user_id}}
    return {"items":[dict(r) for r in rows],"metrics":metrics,"by_status":by_status,"by_preparer":preparers,"by_reviewer":reviewers,"review_bottlenecks":{"manager":by_status["manager_review"],"partner":by_status["partner_review"]},"filing":{s:sum(r["filing_status"]==s for r in rows) for s in FILING_TRANSITIONS}}

def portal_returns(principal, scope=None):
    scope=scope or portal_scope(principal.account_id)
    with engine.connect() as c: ids=list(c.scalars(select(tax_engagement_returns.c.id).join(tax_engagements).where(or_(tax_engagements.c.person_id.in_(scope["person_ids"]),tax_engagements.c.household_id.in_(scope["shared_household_ids"])))))
    return [return_detail(i) for i in ids]
