from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from app.portfolio.adapters import SchwabCsvAdapter
from app.services.portfolio import search_portfolios
from app.services.portfolio_import import import_portfolio_file

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

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
