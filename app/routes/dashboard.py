from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from app.db import (
    accounts,
    engine,
    households,
    people,
    person_source_links,
    source_contacts,
)


router = APIRouter()


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


@router.get("/health")
def health():
    return {
        "status": "ok",
        "application": "Client360",
    }


@router.get("/api/stats")
def stats():
    return get_database_stats()


@router.get("/", response_class=HTMLResponse)
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
                grid-template-columns: repeat(
                    auto-fit,
                    minmax(210px, 1fr)
                );
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
                    <div class="value">
                        {stats["source_contacts"]:,}
                    </div>
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
                    <div class="value">
                        {stats["source_links"]:,}
                    </div>
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
