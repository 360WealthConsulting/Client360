from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from app.db import (
    accounts,
    engine,
    people,
    person_source_links,
    source_contacts,
)
from app.services.documents import get_person_documents
from app.services.timeline import get_person_timeline


router = APIRouter()


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
def person_profile(person_id: int):
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

    with engine.connect() as connection:
        person = connection.execute(
            person_statement
        ).mappings().one_or_none()

        if person is None:
            return HTMLResponse(
                "<h1>Person not found</h1>",
                status_code=404,
            )

        source_rows = connection.execute(
            source_statement
        ).mappings().all()

        account_rows = connection.execute(
            account_statement
        ).mappings().all()

        timeline_events = get_person_timeline(
            connection,
            person_id,
        )

        documents = get_person_documents(person_id)

    name = person["full_name"] or f"Person {person_id}"

    address_lines = [
        person["address_line_1"],
        person["address_line_2"],
        ", ".join(
            value
            for value in [
                person["city"],
                person["state"],
                person["postal_code"],
            ]
            if value
        ),
    ]

    address = "<br>".join(
        escape(value)
        for value in address_lines
        if value
    ) or "Not available"

    source_cards = ""

    for row in source_rows:
        source_location = ", ".join(
            value
            for value in [row["city"], row["state"]]
            if value
        )

        source_cards += f"""
            <div class="card">
                <h3>{escape(row["source_system"])}</h3>
                <p><strong>Name:</strong> {escape(row["full_name"] or "")}</p>
                <p><strong>Email:</strong> {escape(row["email"] or "")}</p>
                <p><strong>Phone:</strong> {escape(row["phone"] or "")}</p>
                <p><strong>Location:</strong> {escape(source_location)}</p>
                <p><strong>Match method:</strong> {escape(row["match_method"] or "")}</p>
                <p><strong>Confirmed:</strong> {"Yes" if row["confirmed"] else "No"}</p>
                <a href="/source/{row['id']}">View source record</a>
            </div>
        """

    if not source_cards:
        source_cards = """
            <div class="card">
                <p>No linked source records.</p>
            </div>
        """

    account_rows_html = ""

    for row in account_rows:
        total_value = (
            f"${row['total_value']:,.2f}"
            if row["total_value"] is not None
            else ""
        )

        account_rows_html += f"""
            <tr>
                <td>{escape(row["custodian"] or "")}</td>
                <td>{escape(row["account_name"] or "")}</td>
                <td>{escape(row["registration_type"] or "")}</td>
                <td>{escape(row["status"] or "")}</td>
                <td>{total_value}</td>
            </tr>
        """

    if not account_rows_html:
        account_rows_html = """
        <tr>
            <td colspan="5">No accounts linked.</td>
        </tr>
    """

    documents_html = ""

    for document in documents:
        size_kb = document["size"] / 1024

        documents_html += f"""
            <div class="card">
                <h3>{escape(document["name"])}</h3>
                <p><strong>Size:</strong> {size_kb:,.1f} KB</p>
                <p><strong>Path:</strong> {escape(document["path"])}</p>
            </div>
        """

    if not documents_html:
        documents_html = """
            <div class="card">
                <p>No documents found for this person.</p>
            </div>
        """

    timeline_html = ""

    for event in timeline_events:
        occurred_at = event["occurred_at"]
        occurred_text = (
            occurred_at.strftime("%B %-d, %Y at %-I:%M %p")
            if occurred_at
            else "Date unavailable"
        )

        timeline_html += f"""
            <div class="card">
                <h3>{escape(event["title"])}</h3>
                <p><strong>{escape(occurred_text)}</strong></p>
                <p>{escape(event["details"])}</p>
            </div>
        """

    if not timeline_html:
        timeline_html = """
            <div class="card">
                <p>No timeline events found.</p>
            </div>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{escape(name)} - Client360</title>
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

            .top-link {{
                display: inline-block;
                margin-bottom: 20px;
            }}

            .profile-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
                gap: 20px;
                margin-bottom: 32px;
            }}

            .card {{
                background: white;
                padding: 22px;
                border-radius: 10px;
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
            }}

            .sources {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
                gap: 20px;
                margin-bottom: 32px;
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
        </style>
    </head>

    <body>
        <header>
            <h1>{escape(name)}</h1>
            <p>Canonical person record #{person_id}</p>
        </header>

        <main>
            <a class="top-link" href="/people">← Back to people</a>

            <div class="profile-grid">
                <div class="card">
                    <h2>Contact</h2>
                    <p><strong>Email:</strong> {escape(person["primary_email"] or "Not available")}</p>
                    <p><strong>Phone:</strong> {escape(person["primary_phone"] or "Not available")}</p>
                    <p><strong>Preferred name:</strong> {escape(person["preferred_name"] or "Not available")}</p>
                </div>

                <div class="card">
                    <h2>Address</h2>
                    <p>{address}</p>
                </div>

                <div class="card">
                    <h2>Details</h2>
                    <p><strong>Birth date:</strong> {escape(str(person["birth_date"] or "Not available"))}</p>
                    <p><strong>Contact type:</strong> {escape(person["contact_type"] or "Not available")}</p>
                    <p><strong>Active:</strong> {"Yes" if person["active"] else "No"}</p>
                </div>
            </div>

            <h2>Linked Source Records</h2>
            <div class="sources">
                {source_cards}
            </div>

    
                        <h2>Accounts</h2>
            <table>
    <thead>
        <tr>
            <th>Custodian</th>
            <th>Account</th>
            <th>Registration</th>
            <th>Status</th>
            <th>Total Value</th>
        </tr>
    </thead>
    <tbody>
        {account_rows_html}
    </tbody>
</table>

            <h2>Documents</h2>
<div class="sources">
    {documents_html}
</div>

            <h2>Timeline</h2>
            <div class="sources">
                {timeline_html}
            </div>
        </main>
    </body>
    </html>
    """
