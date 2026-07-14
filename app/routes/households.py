from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import (
    engine,
    household_relationships,
    households,
    people,
)
from app.security.authorization import accessible_person_ids


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/households", response_class=HTMLResponse)
def household_directory(request: Request):
    member_count = func.count(
        household_relationships.c.person_id
    ).label("member_count")

    statement = (
        select(
            households.c.id,
            households.c.name,
            households.c.city,
            households.c.state,
            member_count,
        )
        .select_from(
            households.outerjoin(
                household_relationships,
                household_relationships.c.household_id
                == households.c.id,
            )
        )
        .group_by(
            households.c.id,
            households.c.name,
            households.c.city,
            households.c.state,
        )
        .order_by(households.c.name)
    )

    with engine.connect() as connection:
        household_rows = connection.execute(
            statement
        ).mappings().all()

    return templates.TemplateResponse(
        request=request,
        name="households/index.html",
        context={
            "households": household_rows,
            "created": request.query_params.get("created") == "1",
        },
    )


@router.post("/households")
async def create_household(request: Request):
    body = (await request.body()).decode("utf-8")
    form_data = parse_qs(body)

    name = form_data.get("name", [""])[0].strip()
    city = form_data.get("city", [""])[0].strip()
    state = form_data.get("state", [""])[0].strip()

    if not name:
        return HTMLResponse(
            "<h1>Household name is required</h1>",
            status_code=400,
        )

    with engine.begin() as connection:
        household_id = connection.execute(
            insert(households)
            .values(
                name=name,
                city=city or None,
                state=state or None,
            )
            .returning(households.c.id)
        ).scalar_one()

    return RedirectResponse(
        url=f"/households/{household_id}?created=1",
        status_code=303,
    )


@router.get(
    "/households/{household_id}",
    response_class=HTMLResponse,
)
def household_profile(
    request: Request,
    household_id: int,
):
    with engine.connect() as connection:
        household = connection.execute(
            select(households).where(
                households.c.id == household_id
            )
        ).mappings().one_or_none()

        if household is None:
            return HTMLResponse(
                "<h1>Household not found</h1>",
                status_code=404,
            )

        members = connection.execute(
            select(
                people.c.id,
                people.c.full_name,
                people.c.primary_email,
                people.c.primary_phone,
                household_relationships.c.relationship_type,
                household_relationships.c.is_primary,
                household_relationships.c.is_primary_household,
            )
            .select_from(
                household_relationships.join(
                    people,
                    people.c.id
                    == household_relationships.c.person_id,
                )
            )
            .where(
                household_relationships.c.household_id
                == household_id
            )
            .order_by(
                household_relationships.c.is_primary.desc(),
                people.c.last_name,
                people.c.first_name,
            )
        ).mappings().all()

        available_statement = (
            select(
                people.c.id,
                people.c.full_name,
                people.c.primary_email,
            )
            .where(
                ~people.c.id.in_(
                    select(
                        household_relationships.c.person_id
                    ).where(
                        household_relationships.c.household_id
                        == household_id
                    )
                )
            )
        )
        allowed_person_ids = accessible_person_ids(connection, request.state.principal)
        if allowed_person_ids is not None:
            available_statement = available_statement.where(
                people.c.id.in_(allowed_person_ids)
            )
        available_people = connection.execute(
            available_statement.order_by(
                people.c.last_name,
                people.c.first_name,
            )
        ).mappings().all()

    return templates.TemplateResponse(
        request=request,
        name="households/profile.html",
        context={
            "household": household,
            "members": members,
            "available_people": available_people,
            "created": request.query_params.get("created") == "1",
            "member_saved": (
                request.query_params.get("member_saved") == "1"
            ),
        },
    )


@router.post("/households/{household_id}/members")
async def save_household_member(
    request: Request,
    household_id: int,
):
    body = (await request.body()).decode("utf-8")
    form_data = parse_qs(body)

    person_id_text = form_data.get(
        "person_id",
        [""],
    )[0].strip()

    relationship_type = form_data.get(
        "relationship_type",
        ["member"],
    )[0].strip()

    is_primary = (
        form_data.get("is_primary", [""])[0] == "on"
    )
    is_primary_household = (
        form_data.get("is_primary_household", [""])[0] == "on"
    )

    if not person_id_text.isdigit():
        return HTMLResponse(
            "<h1>A valid person is required</h1>",
            status_code=400,
        )

    person_id = int(person_id_text)

    with engine.begin() as connection:
        household_exists = connection.execute(
            select(households.c.id).where(
                households.c.id == household_id
            )
        ).scalar_one_or_none()

        person_exists = connection.execute(
            select(people.c.id).where(
                people.c.id == person_id
            )
        ).scalar_one_or_none()

        if household_exists is None:
            return HTMLResponse(
                "<h1>Household not found</h1>",
                status_code=404,
            )

        if person_exists is None:
            return HTMLResponse(
                "<h1>Person not found</h1>",
                status_code=404,
            )

        if is_primary:
            connection.execute(
                update(household_relationships)
                .where(
                    household_relationships.c.household_id
                    == household_id
                )
                .values(is_primary=False)
            )

        if is_primary_household:
            connection.execute(
                update(household_relationships)
                .where(household_relationships.c.person_id == person_id)
                .values(is_primary_household=False)
            )

        connection.execute(
            pg_insert(household_relationships)
            .values(
                household_id=household_id,
                person_id=person_id,
                relationship_type=relationship_type,
                is_primary=is_primary,
                is_primary_household=is_primary_household,
            )
            .on_conflict_do_update(
                constraint="uq_household_relationship_person",
                set_={
                    "relationship_type": relationship_type,
                    "is_primary": is_primary,
                    "is_primary_household": is_primary_household,
                },
            )
        )

        if is_primary_household:
            connection.execute(
                update(people)
                .where(people.c.id == person_id)
                .values(household_id=household_id)
            )

    return RedirectResponse(
        url=(
            f"/households/{household_id}"
            "?member_saved=1"
        ),
        status_code=303,
    )
