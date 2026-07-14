from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.db import (
    accounts,
    activities,
    engine,
    households,
    people,
    person_source_links,
    source_contacts,
    tasks,
)
from app.services.advisor_ai import build_advisor_recommendations
from app.services.client_alerts import build_client_alerts
from app.services.client_summary import get_client_summary
from app.services.documents import get_person_documents
from app.services.microsoft_documents import get_person_microsoft_documents
from app.services.timeline import get_person_timeline


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/people", response_class=HTMLResponse)
def people_directory():
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

    table_rows = ""

    for row in rows:
        name = row["full_name"] or f"Person {row['id']}"
        location = ", ".join(
            value
            for value in [row["city"], row["state"]]
            if value
        )

        table_rows += f"""
            <tr>
                <td>
                    <a href="/people/{row['id']}">
                        {escape(name)}
                    </a>
                </td>
                <td>{escape(row["primary_email"] or "")}</td>
                <td>{escape(row["primary_phone"] or "")}</td>
                <td>{escape(location)}</td>
                <td>{row["source_count"]}</td>
                <td>{row["account_count"]}</td>
            </tr>
        """

    if not table_rows:
        table_rows = """
            <tr>
                <td colspan="6">No canonical people found.</td>
            </tr>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Client360 People</title>
        <style>
            body {{
                margin: 0;
                font-family: Arial, sans-serif;
                background: #f3f4f6;
                color: #1f2937;
            }}

            header {{
                background: #111827;
                color: white;
                padding: 28px 40px;
            }}

            main {{
                padding: 32px 40px;
            }}

            a {{
                color: #2563eb;
                text-decoration: none;
                font-weight: bold;
            }}

            table {{
                width: 100%;
                border-collapse: collapse;
                background: white;
                border-radius: 10px;
                overflow: hidden;
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
            }}

            th, td {{
                text-align: left;
                padding: 14px 16px;
                border-bottom: 1px solid #e5e7eb;
            }}

            th {{
                background: #f9fafb;
            }}

            tr:last-child td {{
                border-bottom: none;
            }}

            .top-link {{
                display: inline-block;
                margin-bottom: 20px;
            }}
        </style>
    </head>

    <body>
        <header>
            <h1>Canonical People</h1>
            <p>{len(rows)} unified client records</p>
        </header>

        <main>
            <a class="top-link" href="/">← Back to dashboard</a>

            <table>
                <thead>
                    <tr>
                        <th>Name</th>
                        <th>Email</th>
                        <th>Phone</th>
                        <th>Location</th>
                        <th>Sources</th>
                        <th>Accounts</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
        </main>
    </body>
    </html>
    """


@router.get("/people/{person_id}", response_class=HTMLResponse)
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
            return HTMLResponse(
                "<h1>Person not found</h1>",
                status_code=404,
            )

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

    timeline_events = get_person_timeline(
        person_id,
        limit=20,
    )

    documents = get_person_documents(person_id)[:8]
    microsoft_documents = get_person_microsoft_documents(person_id, limit=20)
    client_summary = get_client_summary(person_id)
    client_alerts = build_client_alerts(client_summary)
    advisor_recommendations = build_advisor_recommendations(client_summary)

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
            "client_summary": client_summary,
            "client_alerts": client_alerts,
            "advisor_recommendations": advisor_recommendations,
            "active_tab": tab,
        },
    )
