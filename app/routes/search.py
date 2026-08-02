from fastapi import APIRouter, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select

from app.db import engine, person_source_links, source_contacts

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _search(q: str):
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
                # canonical person, when the contact has been promoted/linked, so results
                # open the Client Profile whenever one is available (else the source record).
                person_source_links.c.person_id,
            )
            .select_from(
                source_contacts.outerjoin(
                    person_source_links,
                    person_source_links.c.source_contact_id == source_contacts.c.id,
                )
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

    # Collapse a canonical person to a single row: a person linked to several source
    # contacts (e.g. the same client in Wealthbox and Schwab) otherwise appears once per
    # system, which reads as duplicates. Unlinked contacts (no person_id) are never merged.
    seen_person_ids: set[int] = set()
    deduped = []
    for row in rows:
        person_id = row["person_id"]
        if person_id is not None:
            if person_id in seen_person_ids:
                continue
            seen_person_ids.add(person_id)
        deduped.append(row)
    return deduped


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
def search_page(request: Request, q: str = "", kind: str = "", active: bool = False,
                archived: bool = False):
    """Universal Search — the single entry point. Resolves people, households, businesses, trusts,
    estates, documents, and tax returns (record-scoped) and opens the correct Client Workspace."""
    from app.services.universal_search import universal_search
    principal = getattr(request.state, "principal", None)
    data = (universal_search(principal, q, types=[kind] if kind else None,
                             active_only=active, include_archived=archived)
            if principal and len(q.strip()) >= 2 else {"query": q, "results": [], "count": 0, "notes": []})
    return templates.TemplateResponse(
        request=request, name="search/universal.html",
        context={"q": q, "data": data, "kind": kind, "active": active, "archived": archived})
