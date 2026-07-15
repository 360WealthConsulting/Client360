from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db import engine, source_contacts
from app.templating import render_error


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/source/{source_contact_id}")
def source_contact_page(request: Request, source_contact_id: int):
    with engine.connect() as connection:
        record = connection.execute(
            select(source_contacts).where(
                source_contacts.c.id == source_contact_id
            )
        ).mappings().first()

    if not record:
        return render_error(request, 404, detail="Record not found.")

    return templates.TemplateResponse(
        request=request,
        name="source/detail.html",
        context={"record": dict(record)},
    )
