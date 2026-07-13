from html import escape

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select

from app.db import engine, source_contacts


router = APIRouter()


@router.get("/api/search")
def search_contacts(
    q: str = Query(min_length=2, max_length=100),
):
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


@router.get("/search", response_class=HTMLResponse)
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
                    value="{escape(q)}"
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
