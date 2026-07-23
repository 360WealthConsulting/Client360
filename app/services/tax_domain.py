from datetime import date, timedelta
import uuid

from sqlalchemy import func, or_, select

from app.db import (engine, filing_jurisdictions, record_assignments, tax_deadline_rules,
    tax_deadlines, tax_engagement_returns, tax_engagements, tax_filing_statuses,
    tax_firms, tax_office_memberships, tax_offices, tax_return_types, tax_seasons,
    tax_workflow_links, tax_years, team_memberships, workflow_instances)
from app.security.audit import write_audit_event
from app.services.timeline import add_timeline_event
from app.services.work_management import assign_work
from app.services.workflow_automation import launch_workflow


def business_due_date(year, month, day, holidays=()):
    due = date(year, month, day)
    holidays = set(holidays)
    while due.weekday() >= 5 or due in holidays:
        due += timedelta(days=1)
    return due


def reference_data():
    with engine.connect() as c:
        def rows(table, order): return [dict(r) for r in c.execute(select(table).order_by(order)).mappings()]
        return {"firms": rows(tax_firms, tax_firms.c.name), "offices": rows(tax_offices, tax_offices.c.name),
            "tax_years": rows(tax_years, tax_years.c.year.desc()), "jurisdictions": rows(filing_jurisdictions, filing_jurisdictions.c.code),
            "return_types": rows(tax_return_types, tax_return_types.c.code), "filing_statuses": rows(tax_filing_statuses, tax_filing_statuses.c.code),
            "seasons": rows(tax_seasons, tax_seasons.c.starts_on)}


def _scope_filter(c, principal):
    if principal.can("record.read_all"):
        return None
    team_ids = list(c.scalars(select(team_memberships.c.team_id).where(team_memberships.c.user_id == principal.user_id)))
    assigned = select(record_assignments.c.entity_id).where(
        record_assignments.c.entity_type == "tax_return", record_assignments.c.inactive_date.is_(None),
        or_(record_assignments.c.user_id == principal.user_id, record_assignments.c.team_id.in_(team_ids) if team_ids else False))
    office_ids = select(tax_office_memberships.c.tax_office_id).where(tax_office_memberships.c.user_id == principal.user_id,
        or_(tax_office_memberships.c.inactive_date.is_(None), tax_office_memberships.c.inactive_date >= date.today()))
    return or_(tax_engagement_returns.c.id.in_(assigned), tax_engagements.c.tax_office_id.in_(office_ids))


def list_engagements(principal, *, tax_year=None, office_id=None, status=None):
    with engine.connect() as c:
        q = select(tax_engagements, tax_engagement_returns.c.id.label("return_id"), tax_engagement_returns.c.status.label("return_status"), tax_return_types.c.code.label("return_type"),
            filing_jurisdictions.c.code.label("jurisdiction"), tax_deadlines.c.due_date, workflow_instances.c.status.label("workflow_status")) \
            .select_from(tax_engagements.join(tax_engagement_returns, tax_engagement_returns.c.tax_engagement_id == tax_engagements.c.id)
                .join(tax_return_types, tax_return_types.c.id == tax_engagement_returns.c.return_type_id)
                .join(filing_jurisdictions, filing_jurisdictions.c.id == tax_engagement_returns.c.jurisdiction_id)) \
            .outerjoin(tax_deadlines, tax_deadlines.c.tax_engagement_return_id == tax_engagement_returns.c.id) \
            .outerjoin(tax_workflow_links, tax_workflow_links.c.tax_engagement_return_id == tax_engagement_returns.c.id) \
            .outerjoin(workflow_instances, workflow_instances.c.id == tax_workflow_links.c.workflow_instance_id)
        scope = _scope_filter(c, principal)
        if scope is not None: q = q.where(scope)
        if tax_year: q = q.join(tax_years).where(tax_years.c.year == tax_year)
        if office_id: q = q.where(tax_engagements.c.tax_office_id == office_id)
        if status: q = q.where(tax_engagement_returns.c.status == status)
        result = []
        for row in c.execute(q.order_by(tax_deadlines.c.due_date.nullslast())).mappings():
            item = dict(row); item["status"] = item.pop("return_status"); result.append(item)
        return result


def dashboard(principal, **filters):
    items = list_engagements(principal, **filters)
    today = date.today()
    return_ids = [i["return_id"] for i in items]
    with engine.connect() as c:
        assigned_ids = set(c.scalars(select(record_assignments.c.entity_id).where(
            record_assignments.c.entity_type == "tax_return",
            record_assignments.c.entity_id.in_(return_ids),
            record_assignments.c.inactive_date.is_(None)))) if return_ids else set()
    # "unassigned" means the return has no active preparer/team assignment — the
    # prior implementation counted a status value ("not_started") that the
    # lifecycle can no longer produce, so it was silently always zero (H11).
    return {"items": items, "metrics": {"engagements": len({i["id"] for i in items}), "returns": len(items),
        "due_30_days": sum(bool(i["due_date"] and today <= i["due_date"] <= today + timedelta(days=30)) for i in items),
        "overdue": sum(bool(i["due_date"] and i["due_date"] < today and i["status"] not in {"filed", "completed"}) for i in items),
        "unassigned": sum(i["return_id"] not in assigned_ids for i in items)},
        "queues": {key: sum(i["status"] == value for i in items) for key, value in {"ready_to_prepare":"ready_to_prepare", "manager_review":"manager_review", "partner_review":"partner_review"}.items()}}


def create_engagement(payload, *, actor_user_id, request_id):
    year = int(payload["tax_year"])
    with engine.begin() as c:
        year_id = c.scalar(select(tax_years.c.id).where(tax_years.c.year == year))
        if not year_id:
            year_id = c.execute(tax_years.insert().values(year=year, starts_on=date(year,1,1), ends_on=date(year,12,31)).returning(tax_years.c.id)).scalar_one()
        firm_id = c.scalar(select(tax_firms.c.id).where(tax_firms.c.code == payload.get("firm_code", "360-tax")))
        office_id = c.scalar(select(tax_offices.c.id).where(tax_offices.c.tax_firm_id == firm_id, tax_offices.c.code == payload.get("office_code", "primary")))
        return_type = c.execute(select(tax_return_types).where(tax_return_types.c.code == payload["return_type"])).mappings().one_or_none()
        jurisdiction_id = c.scalar(select(filing_jurisdictions.c.id).where(filing_jurisdictions.c.code == payload.get("jurisdiction", "US")))
        if not return_type or not firm_id or not office_id or not jurisdiction_id: raise ValueError("Invalid tax reference data")
        filing_status_id = c.scalar(select(tax_filing_statuses.c.id).where(tax_filing_statuses.c.code == payload.get("filing_status", "na")))
        engagement_id = c.execute(tax_engagements.insert().values(tax_firm_id=firm_id, tax_office_id=office_id, tax_year_id=year_id,
            person_id=payload.get("person_id"), household_id=payload.get("household_id"), relationship_entity_id=payload.get("relationship_entity_id"),
            engagement_type=return_type["entity_type"], created_by_user_id=actor_user_id, metadata=payload.get("metadata", {})).returning(tax_engagements.c.id)).scalar_one()
        return_id = c.execute(tax_engagement_returns.insert().values(tax_engagement_id=engagement_id, return_type_id=return_type["id"],
            jurisdiction_id=jurisdiction_id, filing_status_id=filing_status_id, priority=payload.get("priority", "normal"), status="received").returning(tax_engagement_returns.c.id)).scalar_one()
        rule = c.execute(select(tax_deadline_rules).where(tax_deadline_rules.c.jurisdiction_id == jurisdiction_id,
            tax_deadline_rules.c.return_type_id == return_type["id"], tax_deadline_rules.c.published.is_(True)).order_by(tax_deadline_rules.c.version.desc()).limit(1)).mappings().one_or_none()
        if rule:
            calculated = business_due_date(year + 1, rule["month"], rule["day"])
            c.execute(tax_deadlines.insert().values(tax_engagement_return_id=return_id, deadline_rule_id=rule["id"], due_date=calculated, calculated_due_date=calculated))
        # (D.35) Publish the engagement-created business FACT (references only) in the transaction. No
        # financials are stored here; the payload is ids/codes only.
        from app.services.events import publisher
        publisher.publish_safe("tax.engagement_created",
                               {"engagement_id": engagement_id, "return_id": return_id, "tax_year": year,
                                "return_type_code": return_type["code"]}, conn=c, producer="tax.domain",
                               subject_ref=f"tax_return:{return_id}")
    workflow_id = launch_workflow(return_type["workflow_template_code"], actor_user_id=actor_user_id,
        person_id=payload.get("person_id"), household_id=payload.get("household_id"), priority=payload.get("priority", "normal"),
        context={"tax_year": year, "return_type": return_type["code"], "tax_return_id": return_id},
        idempotency_key=f"tax-return:{return_id}:foundation", request_id=request_id)
    with engine.begin() as c: c.execute(tax_workflow_links.insert().values(tax_engagement_return_id=return_id, workflow_instance_id=workflow_id))
    if payload.get("assignee_user_id"):
        assign_work(entity_type="tax_return", entity_id=return_id, assignment_role="primary", user_id=payload["assignee_user_id"], actor_user_id=actor_user_id, reason="Tax engagement launch", request_id=request_id)
    add_timeline_event(source="tax_domain", event_type="tax_engagement_opened", title=f"{return_type['code']} tax engagement opened", person_id=payload.get("person_id"), household_id=payload.get("household_id"), external_id=f"tax-engagement-{engagement_id}-opened", event_metadata={"tax_year":year,"return_id":return_id})
    write_audit_event(action="tax.engagement.created", entity_type="tax_engagement", entity_id=engagement_id, actor_user_id=actor_user_id, request_id=request_id or str(uuid.uuid4()), metadata={"return_id":return_id})
    from app.services.tax_intake import launch_intake
    intake = launch_intake(return_id, actor_user_id=actor_user_id, request_id=request_id)
    return {"engagement_id": engagement_id, "return_id": return_id, "workflow_id": workflow_id, "intake": intake}


def override_deadline(deadline_id, due_date, reason, *, actor_user_id, request_id):
    if not reason.strip(): raise ValueError("Override reason is required")
    with engine.begin() as c:
        result = c.execute(tax_deadlines.update().where(tax_deadlines.c.id == deadline_id).values(due_date=due_date, override_reason=reason, overridden_by_user_id=actor_user_id))
        if not result.rowcount: raise ValueError("Deadline not found")
    write_audit_event(action="tax.deadline.overridden", entity_type="tax_deadline", entity_id=deadline_id, actor_user_id=actor_user_id, request_id=request_id, metadata={"due_date":str(due_date),"reason":reason})


def business_engagements(relationship_entity_id, *, limit=50):
    """Read-only (Phase D.12): a business entity's tax engagements + returns — form type,
    tax year, filing status, and lifecycle status ONLY. The tax domain stores no return
    financial content (no K-1/W-2/QBI/distributions), so those are never returned here.
    Keyed to ``relationship_entity_id``; the caller gates on ``tax.read`` and business scope.
    Bounded."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(tax_engagements.c.id.label("engagement_id"),
                   tax_engagements.c.engagement_type,
                   tax_engagements.c.status.label("engagement_status"),
                   tax_years.c.year.label("tax_year"),
                   tax_engagement_returns.c.id.label("return_id"),
                   tax_engagement_returns.c.status.label("return_status"),
                   tax_return_types.c.code.label("return_type"),
                   tax_return_types.c.form_number,
                   tax_filing_statuses.c.code.label("filing_status"))
            .select_from(tax_engagements
                .outerjoin(tax_years, tax_years.c.id == tax_engagements.c.tax_year_id)
                .outerjoin(tax_engagement_returns,
                           tax_engagement_returns.c.tax_engagement_id == tax_engagements.c.id)
                .outerjoin(tax_return_types,
                           tax_return_types.c.id == tax_engagement_returns.c.return_type_id)
                .outerjoin(tax_filing_statuses,
                           tax_filing_statuses.c.id == tax_engagement_returns.c.filing_status_id))
            .where(tax_engagements.c.relationship_entity_id == relationship_entity_id)
            .order_by(tax_years.c.year.desc().nullslast(), tax_engagements.c.id.desc())
            .limit(limit)).mappings().all()
    return [dict(r) for r in rows]


def client_engagement_summary(person_id, household_id=None):
    """Read-only count of a client's active tax engagements (person, or household).
    Factual composition for the Client 360 summary (Phase D.2); keyed by
    person/household, so it only reflects the requested client."""
    conds = [tax_engagements.c.person_id == person_id]
    if household_id:
        conds.append(tax_engagements.c.household_id == household_id)
    with engine.connect() as conn:
        n = conn.scalar(
            select(func.count()).select_from(tax_engagements)
            .where(or_(*conds), tax_engagements.c.status == "active")
        ) or 0
    return {"active": n}
