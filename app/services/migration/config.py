"""Migration framework configuration — source locations + the report output root.

These are only *paths the framework reads FROM* (inventory/preview) and the report root it writes TO.
Everything here is environment-overridable so the same code runs on the office server and in dev/CI.

Guardrail: ``C:\\From AWS Server`` is the AWS/Drake program backup, NOT the client document library.
It is listed in ``EXCLUDED_ROOTS`` and every filesystem scan skips anything beneath it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Never treated as client documents — the AWS/Drake program backup (see the migration plan).
EXCLUDED_ROOTS: tuple[str, ...] = (r"C:\From AWS Server",)


def is_excluded(path: str | os.PathLike[str]) -> bool:
    """True if ``path`` is at or beneath any excluded root (case-insensitive, separator-agnostic)."""
    p = str(path).replace("/", "\\").lower().rstrip("\\")
    for root in EXCLUDED_ROOTS:
        r = root.replace("/", "\\").lower().rstrip("\\")
        if p == r or p.startswith(r + "\\"):
            return True
    return False


@dataclass(frozen=True)
class MigrationConfig:
    """Resolved source + output locations. Build with :meth:`from_env`."""

    migration_root: Path      # where run artifacts (manifest/reconciliation/exceptions/summary) are written
    wealthbox_export: Path    # raw Wealthbox export folder (contact zips / CSV)
    taxdome_root: Path        # TaxDome Drive (documents) root
    sharepoint_root: Path     # OneDrive-synced SharePoint document library
    scanner_root: Path        # scanner drop-folder repository
    document_root: Path       # existing on-prem Client360 document repository

    @classmethod
    def from_env(cls) -> MigrationConfig:
        g = os.getenv
        return cls(
            migration_root=Path(g("CLIENT360_MIGRATION_ROOT", "Migration")),
            wealthbox_export=Path(g("CLIENT360_WEALTHBOX_EXPORT", "01 Raw Imports/Wealthbox")),
            taxdome_root=Path(g("CLIENT360_TAXDOME_SOURCE_ROOT", "Z:\\")),
            sharepoint_root=Path(g(
                "CLIENT360_SHAREPOINT_SOURCE_ROOT",
                r"C:\Users\michael\OneDrive - 360 Wealth Consulting, LLC")),
            scanner_root=Path(g("CLIENT360_SCANNER_ROOT", r"C:\Shares\Scans")),
            document_root=Path(g("CLIENT360_DOCUMENT_ROOT", r"C:\Client360\Data\Documents")),
        )
