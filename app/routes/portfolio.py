from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.portfolio.adapters import SchwabCsvAdapter
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.portfolio import search_portfolios
from app.services.portfolio_import import import_portfolio_file

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
def portfolio_page(
    request: Request,
    q: str = "",
    min_aum: Optional[float] = None,
    registration: Optional[str] = None,
    high_cash: bool = False,
    missing_beneficiary: bool = False,
    concentration: Optional[float] = None,
    principal: Principal = Depends(require_capability("client.read")),
):
    results = search_portfolios(q, min_aum, registration, high_cash, missing_beneficiary, concentration)
    return templates.TemplateResponse(
        request=request,
        name="portfolio/search.html",
        context={
            "results": results,
            "principal": principal,
            "filters": {
                "q": q, "min_aum": min_aum, "registration": registration,
                "high_cash": high_cash, "missing_beneficiary": missing_beneficiary,
                "concentration": concentration,
            },
        },
    )


@router.get("/search")
def portfolio_search(
    q: str = "",
    min_aum: Optional[float] = None,
    registration: Optional[str] = None,
    high_cash: bool = False,
    missing_beneficiary: bool = False,
    concentration: Optional[float] = None,
):
    return {"results": search_portfolios(q, min_aum, registration, high_cash, missing_beneficiary, concentration)}

@router.post("/import/schwab")
def manual_schwab_import(path: str):
    candidate = Path(path).expanduser().resolve()
    allowed = (Path.cwd() / "01 Raw Imports" / "Schwab").resolve()
    if allowed not in candidate.parents or not candidate.is_file() or candidate.suffix.lower() != ".csv":
        raise HTTPException(400, "Select a Schwab CSV from the managed raw-import folder.")
    return import_portfolio_file(candidate, SchwabCsvAdapter())
