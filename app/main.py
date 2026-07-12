import csv
import os
import hashlib
from html import escape
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.services.person_merge import merge_source_contacts
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
match_review_decisions = metadata.tables["match_review_decisions"]

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
@app.get("/matches/{group_number}", response_class=HTMLResponse)
def match_group_page(group_number: int):
    report_path = Path(
        "06 Reports/private/exact_match_merge_plan.csv"
    )

    if not report_path.exists():
        return HTMLResponse(
            "<h1>Match report not found</h1>",
            status_code=404,
        )

    with report_path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file_handle:
        review_groups = [
            row
            for row in csv.DictReader(file_handle)
            if row.get("decision") == "REVIEW"
        ]

    if group_number < 1 or group_number > len(review_groups):
        return HTMLResponse(
            """
            <h1>Review group not found</h1>
            <p><a href="/matches">← Back to matches</a></p>
            """,
            status_code=404,
        )

    group = review_groups[group_number - 1]

    record_ids = [
        int(value.strip())
        for value in group.get("record_ids", "").split("|")
        if value.strip().isdigit()
    ]

    sorted_record_ids = sorted(record_ids)

    group_key_source = "|".join(
        str(record_id)
        for record_id in sorted_record_ids
    )

    group_key = hashlib.sha256(
        group_key_source.encode("utf-8")
    ).hexdigest()

    with engine.connect() as connection:
        saved_decision = connection.execute(
            select(match_review_decisions).where(
                match_review_decisions.c.group_key == group_key
            )
        ).mappings().first()

    decision_label = (
        saved_decision["decision"].replace("_", " ").title()
        if saved_decision
        else "Pending Review"
    )

    with engine.connect() as connection:
        records = connection.execute(
            select(source_contacts)
            .where(source_contacts.c.id.in_(record_ids))
            .order_by(source_contacts.c.source_system)
        ).mappings().all()

    record_cards = ""

    for record in records:
        def show(value):
            if value is None or value == "":
                return "—"
            return escape(str(value))

        address = ", ".join(
            show(record.get(field))
            for field in [
                "address_line_1",
                "address_line_2",
                "city",
                "state",
                "postal_code",
            ]
            if record.get(field)
        ) or "—"

        record_cards += f"""
        <div class="record-card">
            <h2>{show(record.get("source_system"))}</h2>

            <p>
                <a href="/source/{record["id"]}">
                    Open full source record
                </a>
            </p>

            <div class="row">
                <strong>Name</strong>
                <span>{show(record.get("full_name"))}</span>
            </div>

            <div class="row">
                <strong>Email</strong>
                <span>{show(record.get("email"))}</span>
            </div>

            <div class="row">
                <strong>Phone</strong>
                <span>{show(record.get("phone"))}</span>
            </div>

            <div class="row">
                <strong>Address</strong>
                <span>{address}</span>
            </div>

            <div class="row">
                <strong>Territory</strong>
                <span>{show(record.get("territory"))}</span>
            </div>

            <div class="row">
                <strong>Source file</strong>
                <span>{show(record.get("source_file"))}</span>
            </div>
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Review Group {group_number} — Client360</title>

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

            .summary {{
                background: #fff7ed;
                border-left: 4px solid #f97316;
                padding: 18px;
                margin: 20px 0 28px;
            }}

            .records {{
                display: grid;
                grid-template-columns:
                    repeat(auto-fit, minmax(320px, 1fr));
                gap: 20px;
            }}

            .record-card {{
                background: white;
                padding: 24px;
                border-radius: 10px;
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
            }}

            .row {{
                display: grid;
                grid-template-columns: 110px 1fr;
                gap: 12px;
                padding: 11px 0;
                border-bottom: 1px solid #e5e7eb;
            }}

                        .actions {{
                display: flex;
                gap: 12px;
                margin: 0 0 28px;
                flex-wrap: wrap;
            }}

            .actions form {{
                margin: 0;
            }}

            .actions button {{
                border: 0;
                border-radius: 6px;
                padding: 12px 18px;
                color: white;
                font-size: 15px;
                cursor: pointer;
            }}

            .approve {{
                background: #15803d;
            }}

            .reject {{
                background: #b91c1c;
            }}

            .skip {{
                background: #6b7280;
            }}

            a {{
                color: #2563eb;
                text-decoration: none;
            }}
        </style>
    </head>

    <body>
        <header>
            <h1>Review Group {group_number}</h1>
            <p>Compare potential duplicate records</p>
        </header>

        <main>
            <p><a href="/matches">← Back to all matches</a></p>

             <div class="summary">
    <strong>Current decision:</strong>
    {escape(decision_label)}<br><br>

                <strong>Names:</strong>
                {escape(group.get("names", "") or "—")}<br>

                <strong>Sources:</strong>
                {escape(group.get("source_systems", "") or "—")}<br>

                <strong>Reason for review:</strong>
                {escape(group.get("review_reason", "") or "—")}
            </div>

                        <div class="actions">
                <form
                    method="post"
                    action="/matches/{group_number}/decision/approved"
                >
                    <button class="approve" type="submit">
                        Approve Match
                    </button>
                </form>

                <form
                    method="post"
                    action="/matches/{group_number}/decision/rejected"
                >
                    <button class="reject" type="submit">
                        Not a Duplicate
                    </button>
                </form>

                <form
                    method="post"
                    action="/matches/{group_number}/decision/skipped"
                >
                    <button class="skip" type="submit">
                        Skip
                    </button>
                </form>
            </div>

            <div class="records">
                {record_cards}
            </div>
        </main>
    </body>
    </html>
    """

@app.post("/matches/{group_number}/decision/{decision}")
def save_match_decision(group_number: int, decision: str):
    allowed_decisions = {
        "approved",
        "rejected",
        "skipped",
    }

    if decision not in allowed_decisions:
        return HTMLResponse(
            "<h1>Invalid match decision</h1>",
            status_code=400,
        )

    report_path = Path(
        "06 Reports/private/exact_match_merge_plan.csv"
    )

    if not report_path.exists():
        return HTMLResponse(
            "<h1>Match report not found</h1>",
            status_code=404,
        )

    with report_path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file_handle:
        review_groups = [
            row
            for row in csv.DictReader(file_handle)
            if row.get("decision") == "REVIEW"
        ]

    if group_number < 1 or group_number > len(review_groups):
        return HTMLResponse(
            "<h1>Review group not found</h1>",
            status_code=404,
        )

    group = review_groups[group_number - 1]

    record_ids = sorted(
        int(value.strip())
        for value in group.get("record_ids", "").split("|")
        if value.strip().isdigit()
    )

    group_key_source = "|".join(
        str(record_id)
        for record_id in record_ids
    )

    group_key = hashlib.sha256(
        group_key_source.encode("utf-8")
    ).hexdigest()

    with engine.connect() as connection:
        existing_decision = connection.execute(
            select(match_review_decisions.c.decision).where(
                match_review_decisions.c.group_key == group_key
            )
        ).scalar_one_or_none()

    if existing_decision == "approved" and decision != "approved":
        return HTMLResponse(
            "<h1>Approved merge is locked</h1>"
            "<p>This group has already been merged into a canonical "
            "person. An unmerge workflow is required before changing "
            "the decision.</p>",
            status_code=409,
        )

    statement = (
        pg_insert(match_review_decisions)
        .values(
            group_key=group_key,
            record_ids=record_ids,
            decision=decision,
            reviewed_by="Michael Shelton",
        )
        .on_conflict_do_update(
            index_elements=["group_key"],
            set_={
                "record_ids": record_ids,
                "decision": decision,
                "reviewed_by": "Michael Shelton",
                "reviewed_at": func.now(),
                "updated_at": func.now(),
            },
        )
    )

    if decision == "approved":
        try:
            merge_source_contacts(record_ids)
        except ValueError as exc:
            return HTMLResponse(
                f"<h1>Merge failed</h1><p>{escape(str(exc))}</p>",
                status_code=400,
            )

    with engine.begin() as connection:
        connection.execute(statement)

    next_group = min(
        group_number + 1,
        len(review_groups),
    )

    return RedirectResponse(
        url=f"/matches/{next_group}",
        status_code=303,
    )

@app.get("/matches", response_class=HTMLResponse)
def match_review_page(status: str = "all"):
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

            current_group_keys = []

    for row in review_groups:
        current_record_ids = sorted(
            int(value.strip())
            for value in row.get("record_ids", "").split("|")
            if value.strip().isdigit()
        )

        key_source = "|".join(
            str(record_id)
            for record_id in current_record_ids
        )

        current_group_keys.append(
            hashlib.sha256(
                key_source.encode("utf-8")
            ).hexdigest()
        )

    if current_group_keys:
        with engine.connect() as connection:
            saved_decisions = connection.execute(
                select(
                    match_review_decisions.c.group_key,
                    match_review_decisions.c.decision,
                ).where(
                    match_review_decisions.c.group_key.in_(
                        current_group_keys
                    )
                )
            ).mappings().all()
    else:
        saved_decisions = []

    decision_by_key = {
        row["group_key"]: row["decision"]
        for row in saved_decisions
    }

    approved_count = sum(
        decision == "approved"
        for decision in decision_by_key.values()
    )

    rejected_count = sum(
        decision == "rejected"
        for decision in decision_by_key.values()
    )

    skipped_count = sum(
        decision == "skipped"
        for decision in decision_by_key.values()
    )

    decided_count = (
        approved_count
        + rejected_count
        + skipped_count
    )

    remaining_count = max(
        len(review_groups) - decided_count,
        0,
    )

    completion_percent = (
        decided_count / len(review_groups) * 100
        if review_groups
        else 0
    )
    allowed_statuses = {
        "all",
        "pending",
        "approved",
        "rejected",
        "skipped",
    }

    if status not in allowed_statuses:
        status = "all"

    filtered_review_groups = []

    for group_number, row in enumerate(review_groups, start=1):
        filter_record_ids = sorted(
            int(value.strip())
            for value in row.get("record_ids", "").split("|")
            if value.strip().isdigit()
        )

        filter_key_source = "|".join(
            str(record_id)
            for record_id in filter_record_ids
        )

        filter_group_key = hashlib.sha256(
            filter_key_source.encode("utf-8")
        ).hexdigest()

        filter_decision = decision_by_key.get(
            filter_group_key,
            "pending",
        )

        if status == "all" or filter_decision == status:
            filtered_review_groups.append(
                (group_number, row)
            )

    cards = ""

    for group_number, row in filtered_review_groups:
        record_ids = [
            value.strip()
            for value in row.get("record_ids", "").split("|")
            if value.strip()
        ]

        card_record_ids = sorted(
            int(record_id)
            for record_id in record_ids
            if record_id.isdigit()
        )

        card_key_source = "|".join(
            str(record_id)
            for record_id in card_record_ids
        )

        card_group_key = hashlib.sha256(
            card_key_source.encode("utf-8")
        ).hexdigest()

        card_decision = decision_by_key.get(
            card_group_key,
            "pending",
        )

        card_status = {
            "approved": "Approved",
            "rejected": "Not a Duplicate",
            "skipped": "Skipped",
            "pending": "Pending Review",
        }.get(card_decision, "Pending Review")

        record_links = " | ".join(
            f'<a href="/source/{escape(record_id)}">'
            f'Record {escape(record_id)}</a>'
            for record_id in record_ids
        )

        cards += f"""
        <div class="match-card">
                    <div class="status status-{card_decision}">
                {escape(card_status)}
            </div>
                    <div class="match-number">
            <a href="/matches/{group_number}">
    Review group {group_number}
</a>
        </div>

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

                        .filters {{
                display: flex;
                gap: 10px;
                flex-wrap: wrap;
                margin: 20px 0;
            }}

            .filters a {{
                background: white;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                padding: 9px 14px;
                color: #1f2937;
                font-weight: bold;
                text-decoration: none;
            }}

            .filters a:hover {{
                background: #f3f4f6;
            }}

            .summary {{
                background: #fff7ed;
                border-left: 4px solid #f97316;
                padding: 18px;
                margin-bottom: 24px;
            }}
            .status {{
                display: inline-block;
                padding: 6px 10px;
                margin-bottom: 12px;
                border-radius: 999px;
                font-size: 13px;
                font-weight: bold;
            }}

            .status-approved {{
                background: #dcfce7;
                color: #166534;
            }}

            .status-rejected {{
                background: #fee2e2;
                color: #991b1b;
            }}

            .status-skipped {{
                background: #e5e7eb;
                color: #374151;
            }}

            .status-pending {{
                background: #fef3c7;
                color: #92400e;
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

            <div class="filters">
    <a href="/matches?status=all">All</a>
    <a href="/matches?status=pending">Pending</a>
    <a href="/matches?status=approved">Approved</a>
    <a href="/matches?status=rejected">Not a Duplicate</a>
    <a href="/matches?status=skipped">Skipped</a>
</div>

                        <div class="summary">
                <strong>{len(review_groups):,} total review groups</strong><br>
                Approved: {approved_count:,}<br>
                Not duplicates: {rejected_count:,}<br>
                Skipped: {skipped_count:,}<br>
                Remaining: {remaining_count:,}<br>
                Progress: {completion_percent:.1f}%<br><br>

                Review decisions are saved. Client records are not
                merged or changed by this page.
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
