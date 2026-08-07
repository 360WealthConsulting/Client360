"""Inventory engine — read-only census of every legacy source before any migration.

Covers: Wealthbox export, TaxDome Drive, SharePoint/OneDrive library, scanner repositories, and the
existing on-prem Client360 document repository. For each it reports object/document counts, estimated
storage, a cheap duplicate indicator (files sharing name+size — full SHA-256 dedup happens at import),
source metadata, and a migration-readiness verdict.

Strictly read-only: it walks folders and (for the existing repo) runs a single ``SELECT count`` — it
never writes to Client360, never moves/copies/hashes files, and never touches ``C:\\From AWS Server``
(the AWS/Drake backup, excluded via :func:`config.is_excluded`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from app.services.migration.base import MigrationJob, Mode, Outcome
from app.services.migration.config import MigrationConfig, is_excluded

_DOC_EXTS = frozenset({".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                       ".txt", ".csv", ".tif", ".tiff", ".jpg", ".jpeg", ".png", ".msg", ".eml"})


@dataclass
class SourceInventory:
    source: str
    available: bool
    path: str = ""
    object_counts: dict = field(default_factory=dict)
    document_count: int = 0
    total_bytes: int = 0
    duplicate_groups: int = 0            # groups of files sharing (name, size)
    duplicate_files: int = 0             # excess files across those groups
    zero_byte_files: int = 0
    unreadable_files: int = 0
    metadata: dict = field(default_factory=dict)
    readiness: str = "unknown"
    reasons: list[str] = field(default_factory=list)


def _scan_tree(root: Path) -> dict:
    """Read-only walk of ``root``: file/dir counts, bytes, zero-byte, unreadable, doc count, and a
    name+size duplicate indicator. Skips excluded roots. Enumeration only — never opens file content."""
    files = dirs = total = zero = unreadable = docs = 0
    seen: dict[tuple[str, int], int] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if is_excluded(dirpath):
            dirnames[:] = []
            continue
        # prune excluded subdirectories in place
        dirnames[:] = [d for d in dirnames if not is_excluded(os.path.join(dirpath, d))]
        dirs += len(dirnames)
        for name in filenames:
            files += 1
            fp = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(fp)
            except OSError:
                unreadable += 1
                continue
            total += size
            if size == 0:
                zero += 1
            if os.path.splitext(name)[1].lower() in _DOC_EXTS:
                docs += 1
            key = (name.lower(), size)
            seen[key] = seen.get(key, 0) + 1
    dup_groups = sum(1 for n in seen.values() if n > 1)
    dup_files = sum(n - 1 for n in seen.values() if n > 1)
    return {"files": files, "dirs": dirs, "bytes": total, "zero_byte": zero,
            "unreadable": unreadable, "documents": docs,
            "duplicate_groups": dup_groups, "duplicate_files": dup_files}


def _readiness(available: bool, files: int) -> tuple[str, list[str]]:
    if not available:
        return "unavailable", ["path not found — needs mount / credentials / export"]
    if files == 0:
        return "empty", ["path present but no files found"]
    return "ready", []


def _fs_source(source: str, root: Path, *, count_docs_as_objects: bool = True) -> SourceInventory:
    available = root.exists()
    inv = SourceInventory(source=source, available=available, path=str(root))
    if not available:
        inv.readiness, inv.reasons = _readiness(False, 0)
        return inv
    s = _scan_tree(root)
    inv.total_bytes = s["bytes"]
    inv.document_count = s["documents"]
    inv.duplicate_groups = s["duplicate_groups"]
    inv.duplicate_files = s["duplicate_files"]
    inv.zero_byte_files = s["zero_byte"]
    inv.unreadable_files = s["unreadable"]
    inv.object_counts = {"files": s["files"], "folders": s["dirs"]}
    if count_docs_as_objects:
        inv.object_counts["documents"] = s["documents"]
    inv.readiness, inv.reasons = _readiness(True, s["files"])
    return inv


def inventory_wealthbox(cfg: MigrationConfig) -> SourceInventory:
    root = cfg.wealthbox_export
    inv = _fs_source("Wealthbox", root, count_docs_as_objects=False)
    if inv.available:
        zips = sorted(p.name for p in root.glob("*contacts*.zip"))
        csvs = sorted(p.name for p in root.glob("*.csv"))
        inv.object_counts["contact_export_zips"] = len(zips)
        inv.object_counts["csv_files"] = len(csvs)
        inv.metadata["export_files"] = (zips + csvs)[:20]
        if not zips and not csvs:
            inv.readiness = "needs-export"
            inv.reasons = ["no Wealthbox export present — run a read-only API/CSV export first"]
        else:
            inv.readiness = "ready-to-extract"
            inv.reasons = ["record counts are established by the extraction step (E1), not by file scan"]
    return inv


def inventory_taxdome(cfg: MigrationConfig) -> SourceInventory:
    return _fs_source("TaxDome", cfg.taxdome_root)


def inventory_sharepoint(cfg: MigrationConfig) -> SourceInventory:
    inv = _fs_source("SharePoint/OneDrive", cfg.sharepoint_root)
    if inv.available:
        inv.reasons.append("OneDrive Files-On-Demand: cloud-only placeholders count but may need "
                           "hydration for byte-accurate size/content")
    return inv


def inventory_scanner(cfg: MigrationConfig) -> SourceInventory:
    return _fs_source("Scanner", cfg.scanner_root)


def inventory_existing_docs(cfg: MigrationConfig) -> SourceInventory:
    inv = _fs_source("Client360 Existing Documents", cfg.document_root)
    # Read-only canonical count from the documents table (SELECT only; tolerate an unreachable DB).
    try:
        from sqlalchemy import func, select

        from app.db import documents, engine
        with engine.connect() as c:
            inv.metadata["canonical_documents_in_db"] = int(
                c.execute(select(func.count()).select_from(documents)).scalar_one())
    except Exception as exc:  # noqa: BLE001 — inventory must never fail on DB access
        inv.metadata["canonical_documents_in_db"] = None
        inv.reasons.append(f"documents-table count unavailable: {exc}")
    return inv


ALL_PROVIDERS = {
    "wealthbox": inventory_wealthbox,
    "taxdome": inventory_taxdome,
    "sharepoint": inventory_sharepoint,
    "scanner": inventory_scanner,
    "existing_docs": inventory_existing_docs,
}


def collect_inventory(cfg: MigrationConfig, sources: list[str] | None = None) -> list[SourceInventory]:
    names = sources or list(ALL_PROVIDERS)
    return [ALL_PROVIDERS[n](cfg) for n in names if n in ALL_PROVIDERS]


class InventoryJob(MigrationJob):
    """Runs the whole inventory census as an ``inventory``-mode :class:`MigrationJob` so it emits the
    standard four artifacts. Only the ``inventory`` mode is meaningful here (read-only, no writes)."""

    source_system = "Inventory"
    supported_modes = frozenset({Mode.INVENTORY})

    def _inventory(self, sources: list[str] | None = None, **_opts) -> Outcome:
        invs = collect_inventory(self.config, sources)
        recon: list[dict] = []
        exceptions: list[dict] = []
        total_files = total_bytes = total_docs = 0
        for inv in invs:
            total_files += int(inv.object_counts.get("files", 0))
            total_bytes += inv.total_bytes
            total_docs += inv.document_count
            recon.append({
                "source": inv.source, "available": inv.available, "path": inv.path,
                "files": inv.object_counts.get("files", 0),
                "folders": inv.object_counts.get("folders", 0),
                "documents": inv.document_count,
                "estimated_gb": round(inv.total_bytes / (1024 ** 3), 2),
                "duplicate_groups": inv.duplicate_groups, "duplicate_files": inv.duplicate_files,
                "zero_byte": inv.zero_byte_files, "unreadable": inv.unreadable_files,
                "readiness": inv.readiness, "reasons": "; ".join(inv.reasons),
            })
            if inv.readiness in {"unavailable", "needs-export"}:
                exceptions.append({"source": inv.source, "readiness": inv.readiness,
                                   "path": inv.path, "reason": "; ".join(inv.reasons)})
            if inv.unreadable_files:
                exceptions.append({"source": inv.source, "readiness": "unreadable-files",
                                   "path": inv.path, "reason": f"{inv.unreadable_files} unreadable files"})
        counts = {
            "sources_scanned": len(invs),
            "sources_ready": sum(1 for i in invs if i.readiness in {"ready", "ready-to-extract"}),
            "sources_unavailable": sum(1 for i in invs if i.readiness == "unavailable"),
            "total_files": total_files, "total_documents": total_docs,
            "estimated_total_gb": round(total_bytes / (1024 ** 3), 2),
        }
        notes = ["Read-only inventory — no Client360 writes, no file movement.",
                 "C:\\From AWS Server (AWS/Drake backup) is excluded from all scans."]
        return Outcome(counts=counts, exceptions=exceptions, reconciliation=recon, notes=notes)
