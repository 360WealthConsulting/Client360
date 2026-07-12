import csv
import os
from html import escape
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import (
    MetaData,
    create_engine,
    func,
    or_,
    select,
)
load_dotenv("app/.env")

database_url = os.getenv("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL is missing from app/.env")

engine = create_engine(database_url)

metadata = MetaData()
metadata.reflect(bind=engine)

source_contacts = metadata.tables["source_contacts"]
people = metadata.tables["people"]
accounts = metadata.tables["accounts"]
households = metadata.tables["households"]
person_source_links = metadata.tables["person_source_links"]

app = FastAPI(title="Client360")


def get_database_stats():
    with engine.connect() as connection:
        return {
            "source_contacts": connection.scalar(
                select(func.count()).select_from(source_contacts)
            ),
            "people": connection.scalar(
                select(func.count()).select_from(people)
            ),
            "accounts": connection.scalar(
                select(func.count()).select_from(accounts)
            ),
            "households": connection.scalar(
                select(func.count()).select_from(households)
            ),
            "source_links": connection.scalar(
                select(func.count()).select_from(person_source_links)
            ),
        }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "application": "Client360",
    }


@app.get("/api/stats")
def stats():
    return get_database_stats()
@app.get("/api/search")
def search_contacts(q: str = Query(min_length=2, max_length=100)):
    search_term = f"%{q.strip()}%"

    with engine.connect() as connection:
        results = connection.execute(
            select(
                source_contacts.c.id,
                source_contacts.c.source_system,
                source_contacts.c.full_name,
                source_contacts.c.email,
                source_contacts.c.phone,
                source_contacts.c.city,
                source_contacts.c.state,
            )
            .where(
                or_(
                    source_contacts.c.full_name.ilike(search_term),
                    source_contacts.c.first_name.ilike(search_term),
                    source_contacts.c.last_name.ilike(search_term),
                    source_contacts.c.email.ilike(search_term),
                    source_contacts.c.phone.ilike(search_term),
                    source_contacts.c.city.ilike(search_term),
                )
            )
            .order_by(
                source_contacts.c.full_name,
                source_contacts.c.source_system,
            )
            .limit(100)
        ).mappings().all()

    return {
        "query": q,
        "count": len(results),
        "results": [dict(result) for result in results],
    }
@app.get("/source/{source_contact_id}", response_class=HTMLResponse)
def source_contact_page(source_contact_id: int):
    with engine.connect() as connection:
        record = connection.execute(
            select(source_contacts).where(
                source_contacts.c.id == source_contact_id
            )
        ).mappings().first()

    if not record:
        return HTMLResponse(
            "<h1>Record not found</h1>",
            status_code=404,
        )

    def show(value):
        if value is None or value == "":
            return "—"
        return escape(str(value))

    rows = ""

    for key, value in record.items():
        rows += f"""
        <tr>
            <td><strong>{escape(str(key))}</strong></td>
            <td>{show(value)}</td>
        </tr>
        """

    return f"""
    <html>
    <head>
        <title>{show(record.get("full_name"))}</title>

        <style>
            body {{
                font-family: Arial;
                background:#f4f6f8;
                margin:40px;
            }}

            table {{
                border-collapse:collapse;
                width:100%;
                background:white;
            }}

            td {{
                padding:10px;
                border:1px solid #ddd;
            }}

            a {{
                color:#2563eb;
                text-decoration:none;
            }}
        </style>

    </head>

    <body>

    <p><a href="/search">← Back to Search</a></p>

    <h1>{show(record.get("full_name"))}</h1>

    <table>
    {rows}
    </table>

    </body>
    </html>
    """
@app.get("/matches", response_class=HTMLResponse)
def match_review_page():
    report_file = Path(
        "06 Reports/private/exact_match_merge_plan.csv"
    )

    if not report_file.exists():
        return HTMLResponse(
            """
            <h1>Match report not found</h1>
            <p>Run <code>python app/matching/plan_matches.py</code> first.</p>
            <p><a href="/">Back to dashboard</a></p>
            """,
            status_code=404,
        )

    review_groups = []

    with report_file.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file_handle:
        reader = csv.DictReader(file_handle)

        for row in reader:
            if row.get("decision") == "REVIEW":
                review_groups.append(row)

    cards = ""

    for index, row in enumerate(review_groups, start=1):
        record_ids = [
            value.strip()
            for value in row.get("record_ids", "").split("|")
            if value.strip()
        ]

        record_links = " | ".join(
            f'<a href="/source/{escape(record_id)}">'
            f'Record {escape(record_id)}</a>'
            for record_id in record_ids
        )

        cards += f"""
        <div class="match-card">
            <div class="match-number">Review group {index}</div>

            <div class="field">
                <strong>Names:</strong>
                {escape(row.get("names", "") or "—")}
            </div>

            <div class="field">
                <strong>Sources:</strong>
                {escape(row.get("source_systems", "") or "—")}
            </div>

            <div class="field">
                <strong>Email:</strong>
                {escape(row.get("email", "") or "—")}
            </div>

            <div class="field">
                <strong>Phone:</strong>
                {escape(row.get("phone", "") or "—")}
            </div>

            <div class="field">
                <strong>Reason for review:</strong>
                {escape(row.get("review_reason", "") or "—")}
            </div>

            <div class="field">
                <strong>Source records:</strong>
                {record_links or "—"}
            </div>
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Client360 Match Review</title>

        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 0;
                background: #f4f6f8;
                color: #1f2937;
            }}

            header {{
                background: #111827;
                color: white;
                padding: 24px 40px;
            }}

            main {{
                padding: 40px;
                max-width: 1100px;
            }}

            .summary {{
                background: #fff7ed;
                border-left: 4px solid #f97316;
                padding: 18px;
                margin-bottom: 24px;
            }}

            .match-card {{
                background: white;
                padding: 22px;
                margin-bottom: 18px;
                border-radius: 10px;
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
            }}

            .match-number {{
                font-size: 18px;
                font-weight: bold;
                margin-bottom: 14px;
            }}

            .field {{
                margin: 9px 0;
                line-height: 1.5;
            }}

            a {{
                color: #2563eb;
                text-decoration: none;
            }}
        </style>
    </head>

    <body>
        <header>
            <h1>Client360 Match Review</h1>
            <p>Potential duplicate records requiring manual review</p>
        </header>

        <main>
            <p>
                <a href="/">Dashboard</a>
                &nbsp;|&nbsp;
                <a href="/search">Search</a>
            </p>

            <div class="summary">
                <strong>{len(review_groups):,} review groups</strong><br>
                This page is read-only. No records will be merged or changed.
            </div>

            {cards}
        </main>
    </body>
    </html>
    """


@app.get("/search", response_class=HTMLResponse)
def search_page(q: str = ""):
    rows = []

    if len(q.strip()) >= 2:
        search_term = f"%{q.strip()}%"

        with engine.connect() as connection:
            rows = connection.execute(
                select(
                    source_contacts.c.id,
                    source_contacts.c.source_system,
                    source_contacts.c.full_name,
                    source_contacts.c.email,
                    source_contacts.c.phone,
                    source_contacts.c.city,
                    source_contacts.c.state,
                )
                .where(
                    or_(
                        source_contacts.c.full_name.ilike(search_term),
                        source_contacts.c.first_name.ilike(search_term),
                        source_contacts.c.last_name.ilike(search_term),
                        source_contacts.c.email.ilike(search_term),
                        source_contacts.c.phone.ilike(search_term),
                        source_contacts.c.city.ilike(search_term),
                    )
                )
                .order_by(
                    source_contacts.c.full_name,
                    source_contacts.c.source_system,
                )
                .limit(100)
            ).mappings().all()

    result_rows = ""

    for row in rows:
        location = ", ".join(
            value
            for value in [row["city"], row["state"]]
            if value
        )

        result_rows += f"""
        <tr>
    <td>
        <a href="/source/{row['id']}">
            {escape(row["full_name"] or "(No Name)")}
        </a>
    </td>
    <td>{escape(row["source_system"] or "")}</td>
    <td>{escape(row["email"] or "")}</td>
    <td>{escape(row["phone"] or "")}</td>
    <td>{escape(location)}</td>
</tr>
        """

    if q and not rows:
        result_rows = """
        <tr>
            <td colspan="5">No matching records found.</td>
        </tr>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Client360 Search</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 0;
                background: #f4f6f8;
                color: #1f2937;
            }}

            header {{
                background: #111827;
                color: white;
                padding: 24px 40px;
            }}

            main {{
                padding: 40px;
            }}

            form {{
                display: flex;
                gap: 10px;
                margin-bottom: 25px;
            }}

            input {{
                width: 420px;
                padding: 12px;
                font-size: 16px;
                border: 1px solid #d1d5db;
                border-radius: 6px;
            }}

            button {{
                padding: 12px 20px;
                border: 0;
                border-radius: 6px;
                background: #2563eb;
                color: white;
                font-size: 16px;
                cursor: pointer;
            }}

            table {{
                width: 100%;
                border-collapse: collapse;
                background: white;
            }}

            th, td {{
                padding: 12px;
                text-align: left;
                border-bottom: 1px solid #e5e7eb;
            }}

            th {{
                background: #f9fafb;
            }}

            a {{
                color: #2563eb;
                text-decoration: none;
            }}
        </style>
    </head>

    <body>
        <header>
            <h1>Client360 Search</h1>
            <p>Search imported client and prospect records</p>
        </header>

        <main>
            <p><a href="/">← Dashboard</a></p>

            <form method="get" action="/search">
                <input
                    type="text"
                    name="q"
                    value="{q}"
                    placeholder="Name, email, phone, or city"
                    minlength="2"
                    required
                >
                <button type="submit">Search</button>
            </form>

            <p>{len(rows):,} result(s)</p>

            <table>
                <thead>
                    <tr>
                        <th>Name</th>
                        <th>Source</th>
                        <th>Email</th>
                        <th>Phone</th>
                        <th>Location</th>
                    </tr>
                </thead>
                <tbody>
                    {result_rows}
                </tbody>
            </table>
        </main>
    </body>
    </html>
    """
@app.get("/", response_class=HTMLResponse)
def dashboard():
    stats = get_database_stats()

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Client360</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 0;
                background: #f4f6f8;
                color: #1f2937;
            }}

            header {{
                background: #111827;
                color: white;
                padding: 24px 40px;
            }}

            main {{
                padding: 40px;
            }}

            .cards {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
                gap: 20px;
                max-width: 1200px;
            }}

            .card {{
                background: white;
                padding: 24px;
                border-radius: 10px;
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
            }}

            .label {{
                color: #6b7280;
                font-size: 14px;
                margin-bottom: 8px;
            }}

            .value {{
                font-size: 32px;
                font-weight: bold;
            }}

            .notice {{
                margin-top: 30px;
                background: #fff7ed;
                border-left: 4px solid #f97316;
                padding: 18px;
                max-width: 800px;
            }}
        </style>
    </head>

    <body>
        <header>
            <h1>360 Client Management Database</h1>
            <p>Client360 internal dashboard</p>
        </header>

        <main>
            <h2>Database Overview</h2>

            <div class="cards">
                <div class="card">
                    <div class="label">Imported source records</div>
                    <div class="value">{stats["source_contacts"]:,}</div>
                </div>

                <div class="card">
                    <div class="label">Unified people</div>
                    <div class="value">{stats["people"]:,}</div>
                </div>

                <div class="card">
                    <div class="label">Custodian accounts</div>
                    <div class="value">{stats["accounts"]:,}</div>
                </div>

                <div class="card">
                    <div class="label">Households</div>
                    <div class="value">{stats["households"]:,}</div>
                </div>

                <div class="card">
                    <div class="label">Source links</div>
                    <div class="value">{stats["source_links"]:,}</div>
                </div>
            </div>

            <div class="notice">
                This initial dashboard is read-only. It does not merge,
                edit, or delete client records.
            </div>
        </main>
    </body>
    </html>
    """
