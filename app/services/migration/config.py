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

# Never treated as client documents — the AWS/Drake program backup (see the migration plan). General
# scans skip anything beneath it. The Drake provider is the ONE sanctioned exception: it reads the
# EXPLICITLY listed Drake roots below (some of which live under this path) to COUNT client artifacts for
# review — it never imports them.
EXCLUDED_ROOTS: tuple[str, ...] = (r"C:\From AWS Server",)

# Known Drake program roots to inspect read-only for client artifacts (year-versioned installs + AWS copy).
_DEFAULT_DRAKE_ROOTS: tuple[str, ...] = (
    r"C:\DRAKE21", r"C:\DRAKE22", r"C:\DRAKE23", r"C:\DRAKE24", r"C:\DRAKE25",
    r"C:\DRAKE25-Copy from AWS",
    *(rf"C:\From AWS Server\Z Drive\DRAKE{y:02d}" for y in range(14, 26)),
)


def _parse_roots(value: str | None) -> tuple[Path, ...]:
    """Parse a ';'-separated list of paths (env override) into Paths; empty → ()."""
    if not value:
        return ()
    return tuple(Path(p.strip()) for p in value.split(";") if p.strip())


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
    # Added for the extended inventory (defaults keep existing constructors working):
    vault_root: Path = Path("data/vault")                     # VAULT_STORAGE_ROOT (Client360 Vault files)
    drake_roots: tuple[Path, ...] = ()                        # Drake install roots to inspect read-only
    unclassified_search_roots: tuple[Path, ...] = ()          # drives/roots to sweep for unassigned trees

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
            vault_root=Path(g("VAULT_STORAGE_ROOT", "data/vault")),
            drake_roots=(_parse_roots(g("CLIENT360_DRAKE_ROOTS"))
                         or tuple(Path(p) for p in _DEFAULT_DRAKE_ROOTS)),
            unclassified_search_roots=(_parse_roots(g("CLIENT360_UNCLASSIFIED_SEARCH_ROOTS"))
                                       or (Path("C:\\"),)),
        )
