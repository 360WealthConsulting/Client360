from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import uuid
from sqlalchemy import func, or_, select

from app.db import (engine, engagement_letter_templates, people, portal_accounts,
    portal_document_requests, tax_checklist_items, tax_checklist_template_items,
    tax_checklist_templates, tax_engagement_letters, tax_engagement_returns,
    tax_engagements, tax_missing_items, tax_organizer_templates, tax_organizers,
    tax_questionnaire_answers, tax_questionnaire_questions,
    tax_questionnaire_templates, tax_questionnaires, tax_return_types, tax_workflow_links,
    tax_years, workflow_instances, workflow_steps)
from app.portal.service import notify, require_scope, create_document_request
from app.security.audit import write_audit_event
from app.services.tax_domain import list_engagements
from app.services.timeline import add_timeline_event
from app.services.workflow_automation import complete_step

def _return_context(connection, return_id):
    return connection.execute(select(tax_engagement_returns.c.id.label("return_id"), tax_engagement_returns.c.status.label("return_status"),
        tax_engagements.c.id.label("engagement_id"), tax_engagements.c.person_id, func.coalesce(tax_engagements.c.household_id,people.c.household_id).label("household_id"),
        tax_return_types.c.code.label("return_type"), tax_return_types.c.entity_type, tax_years.c.year,
        workflow_instances.c.id.label("workflow_id"))
        .select_from(tax_engagement_returns.join(tax_engagements).outerjoin(people,people.c.id==tax_engagements.c.person_id).join(tax_return_types).join(tax_years, tax_years.c.id==tax_engagements.c.tax_year_id)
            .outerjoin(tax_workflow_links, tax_workflow_links.c.tax_engagement_return_id==tax_engagement_returns.c.id)
            .outerjoin(workflow_instances, workflow_instances.c.id==tax_workflow_links.c.workflow_instance_id))
        .where(tax_engagement_returns.c.id==return_id)).mappings().one_or_none()

def _detail_from_parts(context, letter, organizer, questionnaire, answers, checklist, missing):
    """Assemble the intake-detail structure — identical to intake_detail()."""
    gates={"letter":bool(letter and letter["status"]=="accepted"),"organizer":bool(organizer and organizer["status"]=="completed"),"questionnaire":bool(questionnaire and questionnaire["status"]=="completed"),"documents":all(not i["required"] or i["status"]=="received" for i in checklist)}
    return {"context":dict(context),"letter":dict(letter) if letter else None,"organizer":dict(organizer) if organizer else None,"questionnaire":dict(questionnaire) if questionnaire else None,"answers":[dict(a) for a in answers],"checklist":[dict(i) for i in checklist],"missing":[dict(i) for i in missing],"gates":gates,"client_readiness":round(100*sum(gates.values())/4),"preparer_ready":all(gates.values())}

def _bulk_intake_details(return_ids):
    """Bulk equivalent of intake_detail() over many returns (removes the N+1 in
    staff_dashboard/portal_intakes). Returns {return_id: detail} where each detail
    is structurally identical to intake_detail(return_id)."""
    return_ids=list(dict.fromkeys(return_ids))
    if not return_ids: return {}
    with engine.connect() as c:
        contexts=c.execute(select(tax_engagement_returns.c.id.label("return_id"), tax_engagement_returns.c.status.label("return_status"),
            tax_engagements.c.id.label("engagement_id"), tax_engagements.c.person_id, func.coalesce(tax_engagements.c.household_id,people.c.household_id).label("household_id"),
            tax_return_types.c.code.label("return_type"), tax_return_types.c.entity_type, tax_years.c.year,
            workflow_instances.c.id.label("workflow_id"))
            .select_from(tax_engagement_returns.join(tax_engagements).outerjoin(people,people.c.id==tax_engagements.c.person_id).join(tax_return_types).join(tax_years, tax_years.c.id==tax_engagements.c.tax_year_id)
                .outerjoin(tax_workflow_links, tax_workflow_links.c.tax_engagement_return_id==tax_engagement_returns.c.id)
                .outerjoin(workflow_instances, workflow_instances.c.id==tax_workflow_links.c.workflow_instance_id))
            .where(tax_engagement_returns.c.id.in_(return_ids))).mappings().all()
        engagement_ids={ctx["engagement_id"] for ctx in contexts}
        letters=c.execute(select(tax_engagement_letters).where(tax_engagement_letters.c.tax_engagement_id.in_(engagement_ids))).mappings().all() if engagement_ids else []
        organizers=c.execute(select(tax_organizers).where(tax_organizers.c.tax_engagement_return_id.in_(return_ids))).mappings().all()
        questionnaires=c.execute(select(tax_questionnaires).where(tax_questionnaires.c.tax_engagement_return_id.in_(return_ids))).mappings().all()
        checklist=c.execute(select(tax_checklist_items).where(tax_checklist_items.c.tax_engagement_return_id.in_(return_ids)).order_by(tax_checklist_items.c.id)).mappings().all()
        missing=c.execute(select(tax_missing_items).where(tax_missing_items.c.tax_engagement_return_id.in_(return_ids),tax_missing_items.c.status=="open").order_by(tax_missing_items.c.due_date)).mappings().all()
        q_ids={q["id"] for q in questionnaires}
        answers=c.execute(select(tax_questionnaire_answers).where(tax_questionnaire_answers.c.questionnaire_id.in_(q_ids))).mappings().all() if q_ids else []
    letter_by_eng={}
    for l in letters: letter_by_eng.setdefault(l["tax_engagement_id"], l)
    organizer_by_ret={o["tax_engagement_return_id"]:o for o in organizers}
    questionnaire_by_ret={q["tax_engagement_return_id"]:q for q in questionnaires}
    checklist_by_ret=defaultdict(list)
    for i in checklist: checklist_by_ret[i["tax_engagement_return_id"]].append(i)
    missing_by_ret=defaultdict(list)
    for m in missing: missing_by_ret[m["tax_engagement_return_id"]].append(m)
    answers_by_q=defaultdict(list)
    for a in answers: answers_by_q[a["questionnaire_id"]].append(a)
    result={}
    for ctx in contexts:
        rid=ctx["return_id"]; questionnaire=questionnaire_by_ret.get(rid)
        result[rid]=_detail_from_parts(ctx, letter_by_eng.get(ctx["engagement_id"]), organizer_by_ret.get(rid), questionnaire,
            answers_by_q.get(questionnaire["id"], []) if questionnaire else [], checklist_by_ret.get(rid, []), missing_by_ret.get(rid, []))
    return result

def _audience(entity_type): return "individual" if entity_type == "individual" else "business"

def _snapshot(row, fields): return {key: row[key] for key in fields}

def _portal_accounts_for(connection, person_id):
    return list(connection.scalars(select(portal_accounts.c.id).where(portal_accounts.c.person_id==person_id, portal_accounts.c.status.in_(("active","invited")))))

def _notify_accounts(account_ids, kind, title, return_id):
    for account_id in account_ids:
        notify(account_id, kind, title, entity_type="tax_return", entity_id=return_id,
            idempotency_key=f"tax-intake:{return_id}:{kind}:{account_id}")

def launch_intake(return_id, *, actor_user_id, request_id=None):
    due = date.today()+timedelta(days=14)
    with engine.begin() as c:
        context=_return_context(c, return_id)
        if not context: raise ValueError("Tax return not found")
        existing=c.scalar(select(tax_organizers.c.id).where(tax_organizers.c.tax_engagement_return_id==return_id))
        if existing: return intake_detail(return_id)
        audience=_audience(context["entity_type"])
        letter=c.execute(select(engagement_letter_templates).where(engagement_letter_templates.c.audience.in_((audience,"all")), engagement_letter_templates.c.status=="published").order_by(engagement_letter_templates.c.version.desc()).limit(1)).mappings().one()
        organizer=c.execute(select(tax_organizer_templates).where(tax_organizer_templates.c.audience==audience, tax_organizer_templates.c.status=="published").order_by(tax_organizer_templates.c.version.desc()).limit(1)).mappings().one()
        questionnaire=c.execute(select(tax_questionnaire_templates).where(tax_questionnaire_templates.c.audience==audience, tax_questionnaire_templates.c.status=="published").order_by(tax_questionnaire_templates.c.version.desc()).limit(1)).mappings().one()
        checklist=c.execute(select(tax_checklist_templates).join(tax_return_types).where(tax_return_types.c.entity_type==context["entity_type"], tax_checklist_templates.c.status=="published").order_by(tax_checklist_templates.c.version.desc()).limit(1)).mappings().one_or_none()
        if not checklist:
            checklist=c.execute(select(tax_checklist_templates).where(tax_checklist_templates.c.status=="published").order_by(tax_checklist_templates.c.version.desc()).limit(1)).mappings().one()
        c.execute(tax_engagement_letters.insert().values(tax_engagement_id=context["engagement_id"], template_id=letter["id"], template_snapshot=_snapshot(letter,("code","version","name","audience","body"))))
        c.execute(tax_organizers.insert().values(tax_engagement_return_id=return_id, template_id=organizer["id"], template_snapshot=_snapshot(organizer,("code","version","name","audience","definition")), tax_year=context["year"]))
        questions=c.execute(select(tax_questionnaire_questions).where(tax_questionnaire_questions.c.template_id==questionnaire["id"]).order_by(tax_questionnaire_questions.c.sequence)).mappings().all()
        question_snapshot={"code":questionnaire["code"],"version":questionnaire["version"],"name":questionnaire["name"],"audience":questionnaire["audience"],"questions":[dict(q) for q in questions]}
        c.execute(tax_questionnaires.insert().values(tax_engagement_return_id=return_id, template_id=questionnaire["id"], template_snapshot=question_snapshot))
        items=c.execute(select(tax_checklist_template_items).where(tax_checklist_template_items.c.template_id==checklist["id"]).order_by(tax_checklist_template_items.c.sequence)).mappings().all()
        accounts=_portal_accounts_for(c,context["person_id"])
    for item in items:
        portal_request_id=create_document_request(person_id=context["person_id"],household_id=context["household_id"],title=item["title"],description=item["description"],due_date=due,workflow_instance_id=context["workflow_id"],requested_by_user_id=actor_user_id)
        with engine.begin() as c:
            checklist_id=c.execute(tax_checklist_items.insert().values(tax_engagement_return_id=return_id,template_item_id=item["id"],item_snapshot=_snapshot(item,("item_key","title","description","required","sequence","condition")),required=item["required"],due_date=due,portal_document_request_id=portal_request_id).returning(tax_checklist_items.c.id)).scalar_one()
            if item["required"]: c.execute(tax_missing_items.insert().values(tax_engagement_return_id=return_id,checklist_item_id=checklist_id,item_type="document",title=item["title"],description=item["description"],due_date=due))
    _notify_accounts(accounts,"engagement_ready","Your tax engagement is ready",return_id)
    _notify_accounts(accounts,"organizer_available","Your tax organizer is available",return_id)
    add_timeline_event(person_id=context["person_id"],household_id=context["household_id"],source="tax_intake",event_type="tax_intake_launched",title=f"{context['year']} tax intake launched",external_id=f"tax-intake-{return_id}-launched")
    write_audit_event(action="tax.intake.launched",entity_type="tax_return",entity_id=return_id,actor_user_id=actor_user_id,request_id=request_id or f"tax-intake-{uuid.uuid4()}")
    return intake_detail(return_id)

def _visible_questions(snapshot, answers):
    return [q for q in snapshot.get("questions",[]) if all(answers.get(k)==v for k,v in (q.get("condition") or {}).items())]

def save_questionnaire(return_id, answers, *, portal_principal=None, actor_user_id=None, complete=False, request_id=None):
    with engine.begin() as c:
        context=_return_context(c,return_id); questionnaire=c.execute(select(tax_questionnaires).where(tax_questionnaires.c.tax_engagement_return_id==return_id).with_for_update()).mappings().one_or_none()
        if not context or not questionnaire: raise ValueError("Tax intake not found")
        if portal_principal: require_scope(portal_principal,person_id=context["person_id"],household_id=context["household_id"],permission="tasks")
        for key,value in answers.items():
            current=c.scalar(select(tax_questionnaire_answers.c.id).where(tax_questionnaire_answers.c.questionnaire_id==questionnaire["id"],tax_questionnaire_answers.c.question_key==key))
            values={"value":value,"answered_by_portal_account_id":portal_principal.account_id if portal_principal else None,"updated_at":datetime.now(timezone.utc)}
            if current: c.execute(tax_questionnaire_answers.update().where(tax_questionnaire_answers.c.id==current).values(**values))
            else: c.execute(tax_questionnaire_answers.insert().values(questionnaire_id=questionnaire["id"],question_key=key,**values))
        rows=c.execute(select(tax_questionnaire_answers.c.question_key,tax_questionnaire_answers.c.value).where(tax_questionnaire_answers.c.questionnaire_id==questionnaire["id"])).all(); saved={r.question_key:r.value for r in rows}
        visible=_visible_questions(questionnaire["template_snapshot"],saved); required=[q for q in visible if q["required"]]
        missing=[q["question_key"] for q in required if q["question_key"] not in saved or saved[q["question_key"]] in (None,"")]
        if complete and missing: raise ValueError(f"Required answers missing: {', '.join(missing)}")
        progress=100 if not required else round(100*(len(required)-len(missing))/len(required)); status="completed" if complete else "in_progress"
        c.execute(tax_questionnaires.update().where(tax_questionnaires.c.id==questionnaire["id"]).values(status=status,progress_percent=progress,started_at=questionnaire["started_at"] or datetime.now(timezone.utc),completed_at=datetime.now(timezone.utc) if complete else None,updated_at=datetime.now(timezone.utc)))
    if complete: _milestone(return_id,"questionnaire_completed",actor_user_id,portal_principal,request_id)
    _advance(return_id)
    return {"status":status,"progress_percent":progress,"missing_required":missing}

def save_organizer(return_id, responses, *, portal_principal=None, actor_user_id=None, complete=False, request_id=None):
    with engine.begin() as c:
        context=_return_context(c,return_id); organizer=c.execute(select(tax_organizers).where(tax_organizers.c.tax_engagement_return_id==return_id).with_for_update()).mappings().one_or_none()
        if not context or not organizer: raise ValueError("Tax intake not found")
        if portal_principal: require_scope(portal_principal,person_id=context["person_id"],household_id=context["household_id"],permission="tasks")
        merged=dict(organizer["responses"] or {}); merged.update(responses); sections=(organizer["template_snapshot"].get("definition") or {}).get("sections",[]); answered=sum(bool(merged.get(s)) for s in sections); progress=100 if complete else round(100*answered/max(len(sections),1)); status="completed" if complete else "in_progress"
        c.execute(tax_organizers.update().where(tax_organizers.c.id==organizer["id"]).values(responses=merged,status=status,progress_percent=progress,started_at=organizer["started_at"] or datetime.now(timezone.utc),completed_at=datetime.now(timezone.utc) if complete else None,updated_at=datetime.now(timezone.utc)))
    if complete: _milestone(return_id,"organizer_completed",actor_user_id,portal_principal,request_id)
    _advance(return_id); return {"status":status,"progress_percent":progress}

def accept_letter(return_id, *, portal_principal, metadata=None, request_id=None):
    with engine.begin() as c:
        context=_return_context(c,return_id)
        if not context: raise ValueError("Tax return not found")
        require_scope(portal_principal,person_id=context["person_id"],household_id=context["household_id"],permission="tasks")
        letter=c.execute(select(tax_engagement_letters).where(tax_engagement_letters.c.tax_engagement_id==context["engagement_id"]).with_for_update()).mappings().one_or_none()
        if not letter: raise ValueError("Engagement letter not found")
        if letter["status"]=="accepted": return letter["id"]
        c.execute(tax_engagement_letters.update().where(tax_engagement_letters.c.id==letter["id"]).values(status="accepted",accepted_by_portal_account_id=portal_principal.account_id,accepted_at=datetime.now(timezone.utc),acceptance_metadata=metadata or {}))
    _milestone(return_id,"engagement_letter_accepted",None,portal_principal,request_id); _advance(return_id); return letter["id"]

def sync_documents(return_id):
    # Route each uploaded checklist document through the deterministic matching
    # engine (portal-request provenance -> accepted link -> checklist/missing
    # resolution). Idempotent: re-running returns the existing link (RC11 C2/C5).
    from app.services.tax_document_intelligence import ingest_document, portal_request_signals, compute_missing
    with engine.connect() as c:
        rows=c.execute(select(tax_checklist_items.c.id,portal_document_requests.c.status,portal_document_requests.c.uploaded_document_id).join(portal_document_requests).where(tax_checklist_items.c.tax_engagement_return_id==return_id)).all()
    for row in rows:
        if row.status in ("uploaded","approved") and row.uploaded_document_id:
            with engine.connect() as c: signals=portal_request_signals(c,row.id)
            ingest_document(row.uploaded_document_id,signals)
    compute_missing(return_id)
    _advance(return_id); return intake_detail(return_id)

def _milestone(return_id,event_type,actor_user_id,portal_principal,request_id):
    with engine.connect() as c: context=_return_context(c,return_id)
    add_timeline_event(person_id=context["person_id"],household_id=context["household_id"],source="tax_intake",event_type=event_type,title=event_type.replace("_"," ").title(),external_id=f"tax-intake-{return_id}-{event_type}")
    write_audit_event(action=f"tax.intake.{event_type}",entity_type="tax_return",entity_id=return_id,actor_user_id=actor_user_id,request_id=request_id or f"tax-intake-{uuid.uuid4()}",metadata={"portal_account_id":portal_principal.account_id if portal_principal else None})

def _advance(return_id):
    detail=intake_detail(return_id); workflow_id=detail["context"]["workflow_id"]
    if not workflow_id: return
    gates=detail["gates"]
    with engine.connect() as c: steps=c.execute(select(workflow_steps).where(workflow_steps.c.workflow_instance_id==workflow_id).order_by(workflow_steps.c.sequence)).mappings().all()
    if gates["letter"] and gates["organizer"] and gates["questionnaire"]:
        intake=next((s for s in steps if s["status"]=="active" and (s["definition_snapshot"] or {}).get("step_key")=="intake"),None)
        if intake: complete_step(intake["id"],actor_user_id=None,request_id=f"tax-intake:{return_id}:scope")
    if all(gates.values()):
        with engine.connect() as c: steps=c.execute(select(workflow_steps).where(workflow_steps.c.workflow_instance_id==workflow_id).order_by(workflow_steps.c.sequence)).mappings().all()
        docs=next((s for s in steps if s["status"]=="active" and (s["definition_snapshot"] or {}).get("step_key")=="documents"),None)
        if docs: complete_step(docs["id"],actor_user_id=None,request_id=f"tax-intake:{return_id}:documents")
        with engine.connect() as c: context=_return_context(c,return_id); accounts=_portal_accounts_for(c,context["person_id"])
        _notify_accounts(accounts,"intake_completed","Your tax intake is complete",return_id)

def intake_detail(return_id):
    with engine.connect() as c:
        context=_return_context(c,return_id)
        if not context: raise ValueError("Tax return not found")
        letter=c.execute(select(tax_engagement_letters).where(tax_engagement_letters.c.tax_engagement_id==context["engagement_id"])).mappings().one_or_none()
        organizer=c.execute(select(tax_organizers).where(tax_organizers.c.tax_engagement_return_id==return_id)).mappings().one_or_none()
        questionnaire=c.execute(select(tax_questionnaires).where(tax_questionnaires.c.tax_engagement_return_id==return_id)).mappings().one_or_none()
        checklist=c.execute(select(tax_checklist_items).where(tax_checklist_items.c.tax_engagement_return_id==return_id).order_by(tax_checklist_items.c.id)).mappings().all()
        missing=c.execute(select(tax_missing_items).where(tax_missing_items.c.tax_engagement_return_id==return_id,tax_missing_items.c.status=="open").order_by(tax_missing_items.c.due_date)).mappings().all()
        answers=[]
        if questionnaire: answers=c.execute(select(tax_questionnaire_answers).where(tax_questionnaire_answers.c.questionnaire_id==questionnaire["id"])).mappings().all()
    gates={"letter":bool(letter and letter["status"]=="accepted"),"organizer":bool(organizer and organizer["status"]=="completed"),"questionnaire":bool(questionnaire and questionnaire["status"]=="completed"),"documents":all(not i["required"] or i["status"]=="received" for i in checklist)}
    return {"context":dict(context),"letter":dict(letter) if letter else None,"organizer":dict(organizer) if organizer else None,"questionnaire":dict(questionnaire) if questionnaire else None,"answers":[dict(a) for a in answers],"checklist":[dict(i) for i in checklist],"missing":[dict(i) for i in missing],"gates":gates,"client_readiness":round(100*sum(gates.values())/4),"preparer_ready":all(gates.values())}

def staff_dashboard(principal):
    returns=list_engagements(principal)
    details=_bulk_intake_details([row["return_id"] for row in returns]); items=[]
    for row in returns:
        detail=details.get(row["return_id"])
        if detail is None: continue
        overdue=sum(bool(m["due_date"] and m["due_date"]<date.today()) for m in detail["missing"]); items.append({**row,"intake":detail,"overdue_items":overdue})
    return {"items":items,"metrics":{"returns":len(items),"client_ready":sum(i["intake"]["client_readiness"]==100 for i in items),"preparer_ready":sum(i["intake"]["preparer_ready"] for i in items),"missing_documents":sum(len(i["intake"]["missing"]) for i in items),"overdue_items":sum(i["overdue_items"] for i in items)}}

def portal_intakes(principal, scope=None):
    from app.portal.service import portal_scope
    scope=scope or portal_scope(principal.account_id)
    with engine.connect() as c:
        ids=list(c.scalars(select(tax_engagement_returns.c.id).join(tax_engagements).where(or_(tax_engagements.c.person_id.in_(scope["person_ids"]),tax_engagements.c.household_id.in_(scope["shared_household_ids"])))))
    details=_bulk_intake_details(ids)
    return [details[i] for i in ids if i in details and details[i]["organizer"] is not None]

def _has_intake(return_id):
    with engine.connect() as c: return bool(c.scalar(select(tax_organizers.c.id).where(tax_organizers.c.tax_engagement_return_id==return_id)))

def template_catalog():
    with engine.connect() as c:
        return {"engagement_letters":[dict(r) for r in c.execute(select(engagement_letter_templates).order_by(engagement_letter_templates.c.code,engagement_letter_templates.c.version.desc())).mappings()],
            "organizers":[dict(r) for r in c.execute(select(tax_organizer_templates).order_by(tax_organizer_templates.c.code,tax_organizer_templates.c.version.desc())).mappings()],
            "questionnaires":[dict(r) for r in c.execute(select(tax_questionnaire_templates).order_by(tax_questionnaire_templates.c.code,tax_questionnaire_templates.c.version.desc())).mappings()],
            "checklists":[dict(r) for r in c.execute(select(tax_checklist_templates).order_by(tax_checklist_templates.c.code,tax_checklist_templates.c.version.desc())).mappings()]}

def process_reminders(today=None):
    today=today or date.today(); sent=0
    with engine.connect() as c:
        rows=c.execute(select(tax_missing_items.c.id,tax_missing_items.c.tax_engagement_return_id,tax_missing_items.c.title,tax_engagements.c.person_id)
            .select_from(tax_missing_items.join(tax_engagement_returns,tax_engagement_returns.c.id==tax_missing_items.c.tax_engagement_return_id)
                .join(tax_engagements,tax_engagements.c.id==tax_engagement_returns.c.tax_engagement_id))
            .where(tax_missing_items.c.status=="open",tax_missing_items.c.due_date<=today)).all()
    for row in rows:
        with engine.connect() as c: accounts=_portal_accounts_for(c,row.person_id)
        for account in accounts:
            notify(account,"missing_document",f"Missing tax item: {row.title}",entity_type="tax_missing_item",entity_id=row.id,idempotency_key=f"tax-missing:{row.id}:{today}:{account}"); sent+=1
        with engine.begin() as c: c.execute(tax_missing_items.update().where(tax_missing_items.c.id==row.id).values(reminder_count=tax_missing_items.c.reminder_count+1,last_reminded_at=datetime.now(timezone.utc)))
    with engine.connect() as c:
        questionnaires=c.execute(select(tax_questionnaires.c.id,tax_questionnaires.c.tax_engagement_return_id,tax_engagements.c.person_id)
            .select_from(tax_questionnaires.join(tax_engagement_returns,tax_engagement_returns.c.id==tax_questionnaires.c.tax_engagement_return_id)
                .join(tax_engagements,tax_engagements.c.id==tax_engagement_returns.c.tax_engagement_id))
            .where(tax_questionnaires.c.status!="completed")).all()
    for row in questionnaires:
        with engine.connect() as c: accounts=_portal_accounts_for(c,row.person_id)
        for account in accounts:
            notify(account,"questionnaire_reminder","Please complete your tax questionnaire",entity_type="tax_questionnaire",entity_id=row.id,idempotency_key=f"tax-questionnaire:{row.id}:{today}:{account}"); sent+=1
    return sent
