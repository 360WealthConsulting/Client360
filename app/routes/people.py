from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.templating import render_error

from app.db import (
    accounts,
    activities,
    engine,
    households,
    people,
    person_source_links,
    source_contacts,
    tasks,
    relationship_types,
)
from app.security.authorization import accessible_person_ids
from app.services.advisor_ai import build_advisor_recommendations
from app.services.calendar import get_person_calendar_events
from app.services.client_alerts import build_client_alerts
from app.services.client_summary import get_client_summary
from app.services.documents import get_person_documents
from app.services.microsoft_documents import get_person_microsoft_documents
from app.services.timeline import get_person_timeline
from app.services.relationships import (
    build_relationship_graph,
    get_person_households,
)
from app.services.portfolio import get_person_portfolio


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/people")
def people_directory(request: Request):
    source_count = func.count(
        func.distinct(person_source_links.c.source_contact_id)
    ).label("source_count")

    account_count = func.count(
        func.distinct(accounts.c.id)
    ).label("account_count")

    statement = (
        select(
            people.c.id,
            people.c.full_name,
            people.c.primary_email,
            people.c.primary_phone,
            people.c.city,
            people.c.state,
            source_count,
            account_count,
        )
        .select_from(
            people
            .outerjoin(
                person_source_links,
                person_source_links.c.person_id == people.c.id,
            )
            .outerjoin(
                accounts,
                accounts.c.person_id == people.c.id,
            )
        )
        .group_by(
            people.c.id,
            people.c.full_name,
            people.c.primary_email,
            people.c.primary_phone,
            people.c.city,
            people.c.state,
        )
        .order_by(
            people.c.last_name,
            people.c.first_name,
            people.c.id,
        )
    )

    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()

    return templates.TemplateResponse(
        request=request,
        name="people/directory.html",
        context={"rows": [dict(r) for r in rows], "count": len(rows)},
    )


@router.get("/people/{person_id}")
def person_profile(
    request: Request,
    person_id: int,
    tab: str = "overview",
):
    allowed_tabs = {
        "overview",
        "timeline",
        "tasks",
        "documents",
        "notes",
        "activities",
        "calendar",
        "relationships",
        "portfolio",
    }

    if tab not in allowed_tabs:
        tab = "overview"
    person_statement = select(people).where(
        people.c.id == person_id
    )

    source_statement = (
        select(
            source_contacts.c.id,
            source_contacts.c.source_system,
            source_contacts.c.full_name,
            source_contacts.c.email,
            source_contacts.c.phone,
            source_contacts.c.city,
            source_contacts.c.state,
            person_source_links.c.match_method,
            person_source_links.c.confirmed,
        )
        .select_from(
            person_source_links.join(
                source_contacts,
                source_contacts.c.id
                == person_source_links.c.source_contact_id,
            )
        )
        .where(person_source_links.c.person_id == person_id)
        .order_by(
            source_contacts.c.source_system,
            source_contacts.c.id,
        )
    )

    account_statement = (
        select(accounts)
        .where(accounts.c.person_id == person_id)
        .order_by(
            accounts.c.custodian,
            accounts.c.account_name,
            accounts.c.id,
        )
    )

    activity_statement = (
        select(activities)
        .where(activities.c.person_id == person_id)
        .order_by(
            activities.c.occurred_at.desc(),
            activities.c.id.desc(),
        )
        .limit(20)
    )

    task_statement = (
        select(tasks)
        .where(
            tasks.c.person_id == person_id,
            tasks.c.status != "complete",
        )
        .order_by(
            tasks.c.due_date.asc().nullslast(),
            tasks.c.created_at.desc(),
        )
        .limit(8)
    )

    with engine.connect() as connection:
        person = connection.execute(
            person_statement
        ).mappings().one_or_none()

        if person is None:
            return render_error(request, 404, detail="Person not found.")

        household = None

        if person["household_id"]:
            household = connection.execute(
                select(households).where(
                    households.c.id == person["household_id"]
                )
            ).mappings().one_or_none()

        source_rows = connection.execute(
            source_statement
        ).mappings().all()

        account_rows = connection.execute(
            account_statement
        ).mappings().all()

        open_tasks = connection.execute(
            task_statement
        ).mappings().all()

        activity_rows = connection.execute(
            activity_statement
        ).mappings().all()

        relationship_type_rows = connection.execute(
            select(relationship_types)
            .where(relationship_types.c.active.is_(True))
            .order_by(relationship_types.c.category, relationship_types.c.name)
        ).mappings().all()

        _picker = (
            select(people.c.id, people.c.full_name, people.c.primary_email)
            .where(people.c.id != person_id, people.c.active.is_(True))
        )
        _allowed_ids = accessible_person_ids(connection, request.state.principal)
        if _allowed_ids is not None:
            _picker = _picker.where(people.c.id.in_(_allowed_ids))
        available_people = connection.execute(
            _picker.order_by(people.c.last_name, people.c.first_name)
        ).mappings().all()

    timeline_events = get_person_timeline(
        person_id,
        limit=20,
    )

    documents = get_person_documents(person_id)[:8]
    calendar_events = get_person_calendar_events(person_id, limit=50)
    upcoming_meetings = get_person_calendar_events(
        person_id,
        upcoming_only=True,
        limit=5,
    )
    microsoft_documents = get_person_microsoft_documents(person_id, limit=20)
    client_summary = get_client_summary(person_id)
    portfolio = get_person_portfolio(person_id)
    client_alerts = build_client_alerts(client_summary)
    relationship_graph = build_relationship_graph(person_id)
    person_households = get_person_households(person_id)
    advisor_recommendations = build_advisor_recommendations(
        client_summary,
        relationship_graph=relationship_graph,
        portfolio=portfolio,
    )

    return templates.TemplateResponse(
        request=request,
        name="people/workspace.html",
        context={
            "person": person,
            "household": household,
            "sources": source_rows,
            "accounts": account_rows,
            "open_tasks": open_tasks,
            "all_tasks": open_tasks,
            "timeline_events": timeline_events,
            "documents": documents,
            "microsoft_documents": microsoft_documents,
            "activities": activity_rows,
            "calendar_events": calendar_events,
            "upcoming_meetings": upcoming_meetings,
            "client_summary": client_summary,
            "client_alerts": client_alerts,
            "advisor_recommendations": advisor_recommendations,
            "relationship_graph": relationship_graph,
            "relationship_types": relationship_type_rows,
            "available_people": available_people,
            "person_households": person_households,
            "portfolio": portfolio,
            "active_tab": tab,
        },
    )
