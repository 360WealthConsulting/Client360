from datetime import date
from urllib.parse import parse_qs

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db import engine, relationship_entities, relationship_types
from app.services.relationships import (
    create_relationship,
    deactivate_relationship,
    search_relationships,
)


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _optional_date(value: str):
    return date.fromisoformat(value) if value else None


@router.post("/people/{person_id}/relationships")
async def add_person_relationship(request: Request, person_id: int):
    form = parse_qs((await request.body()).decode("utf-8"))
    value = lambda key, default="": form.get(key, [default])[0].strip()
    target_person = value("target_person_id")
    try:
        create_relationship(
            person_id=person_id,
            relationship_code=value("relationship_code"),
            target_person_id=int(target_person) if target_person.isdigit() else None,
            target_entity_type=value("target_entity_type") or None,
            target_name=value("target_name") or None,
            effective_date=_optional_date(value("effective_date")),
            inactive_date=_optional_date(value("inactive_date")),
            notes=value("notes") or None,
            confidence_level=float(value("confidence_level", "100")),
            source=value("source", "manual"),
            created_by=value("created_by") or None,
        )
    except (ValueError, TypeError) as exc:
        return HTMLResponse(f"<h1>{exc}</h1>", status_code=400)
    return RedirectResponse(
        url=f"/people/{person_id}?tab=relationships&saved=1",
        status_code=303,
    )


@router.post("/relationships/{relationship_id}/deactivate")
def end_relationship(relationship_id: int, person_id: int):
    if not deactivate_relationship(relationship_id):
        return HTMLResponse("<h1>Relationship not found</h1>", status_code=404)
    return RedirectResponse(
        url=f"/people/{person_id}?tab=relationships",
        status_code=303,
    )


@router.get("/relationship-entities/{entity_id}", response_class=HTMLResponse)
def relationship_entity_profile(request: Request, entity_id: int):
    with engine.connect() as connection:
        entity = connection.execute(
            select(relationship_entities).where(
                relationship_entities.c.id == entity_id
            )
        ).mappings().one_or_none()
    if entity is None:
        return HTMLResponse("<h1>Record not found</h1>", status_code=404)
    if entity["person_id"]:
        return RedirectResponse(url=f"/people/{entity['person_id']}", status_code=303)
    if entity["household_id"]:
        return RedirectResponse(url=f"/households/{entity['household_id']}", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="relationships/entity.html",
        context={"entity": entity},
    )


@router.get("/api/relationships/search")
def relationship_search_api(
    relationship_type: str = Query(default="", max_length=100),
    related_name: str = Query(default="", max_length=200),
):
    results = search_relationships(
        relationship_code=relationship_type or None,
        related_name=related_name or None,
    )
    return {"count": len(results), "results": results}


@router.get("/relationships/search", response_class=HTMLResponse)
def relationship_search_page(
    request: Request,
    relationship_type: str = "",
    related_name: str = "",
):
    results = search_relationships(
        relationship_code=relationship_type or None,
        related_name=related_name or None,
    ) if (relationship_type or related_name) else []
    with engine.connect() as connection:
        types = connection.execute(
            select(relationship_types).order_by(relationship_types.c.name)
        ).mappings().all()
    return templates.TemplateResponse(
        request=request,
        name="relationships/search.html",
        context={
            "results": results,
            "types": types,
            "relationship_type": relationship_type,
            "related_name": related_name,
        },
    )
