from fastapi import APIRouter, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select

from app.db import engine, source_contacts


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _search(q: str):
    search_term = f"%{q.strip()}%"
    with engine.connect() as connection:
        return connection.execute(
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


@router.get("/api/search")
def search_contacts(
    q: str = Query(min_length=2, max_length=100),
):
    results = _search(q)
    return {
        "query": q,
        "count": len(results),
        "results": [dict(result) for result in results],
    }


@router.get("/search")
def search_page(request: Request, q: str = ""):
    rows = _search(q) if len(q.strip()) >= 2 else []
    return templates.TemplateResponse(
        request=request,
        name="search/results.html",
        context={"q": q, "rows": [dict(r) for r in rows], "count": len(rows)},
    )
