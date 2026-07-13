from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.db import engine, source_contacts


router = APIRouter()


@router.get(
    "/source/{source_contact_id}",
    response_class=HTMLResponse,
)
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
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta
            name="viewport"
            content="width=device-width, initial-scale=1.0"
        >
        <title>{show(record.get("full_name"))}</title>

        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #f4f6f8;
                margin: 40px;
                color: #1f2937;
            }}

            table {{
                border-collapse: collapse;
                width: 100%;
                background: white;
            }}

            td {{
                padding: 10px;
                border: 1px solid #ddd;
                vertical-align: top;
            }}

            a {{
                color: #2563eb;
                text-decoration: none;
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
