from pathlib import Path
from typing import Protocol
from app.portfolio.models import PortfolioBatch

class PortfolioSourceAdapter(Protocol):
    """Boundary shared by file exports and a future Schwab API adapter."""
    custodian_code: str
    def read(self, path: Path) -> PortfolioBatch: ...
