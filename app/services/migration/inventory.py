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
    breakdown: list[dict] = field(default_factory=list)   # extra per-source reconciliation rows


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


# --- Client360 Vault (DB + files, read-only) ---------------------------------

def inventory_vault(cfg: MigrationConfig) -> SourceInventory:
    """Vault census: DB doc count + linked people/households, physical files, and missing/orphan files
    (a document whose file is gone; a file no vault_document references). Read-only (SELECT + walk)."""
    root = cfg.vault_root
    inv = SourceInventory(source="Client360 Vault", available=root.exists(), path=str(root))
    phys: dict[str, int] = {}
    if inv.available:
        for dp, _dn, fn in os.walk(root):
            for name in fn:
                fp = os.path.join(dp, name)
                rel = os.path.relpath(fp, root).replace(os.sep, "/")
                try:
                    phys[rel] = os.path.getsize(fp)
                except OSError:
                    inv.unreadable_files += 1
    inv.object_counts["physical_files"] = len(phys)
    inv.object_counts["files"] = len(phys)
    inv.total_bytes = sum(phys.values())
    try:
        from sqlalchemy import func, select

        from app.db import engine, vault_document_links, vault_documents
        with engine.connect() as c:
            doc_count = int(c.execute(select(func.count()).select_from(vault_documents)).scalar_one())
            keys = {k for (k,) in c.execute(select(vault_documents.c.storage_key)).all() if k}
            db_bytes = int(c.execute(select(func.coalesce(func.sum(vault_documents.c.file_size), 0))).scalar_one())
            people = int(c.execute(select(func.count(func.distinct(vault_document_links.c.person_id)))
                                   .where(vault_document_links.c.person_id.isnot(None))).scalar_one())
            households = int(c.execute(select(func.count(func.distinct(vault_document_links.c.household_id)))
                                       .where(vault_document_links.c.household_id.isnot(None))).scalar_one())
        missing = sum(1 for k in keys if k not in phys)
        orphan = sum(1 for rel in phys if rel not in keys)
        inv.document_count = doc_count
        inv.object_counts.update({"vault_documents": doc_count, "missing_files": missing, "orphan_files": orphan})
        inv.metadata.update({"db_file_size_bytes": db_bytes, "linked_people": people,
                             "linked_households": households, "missing_files": missing, "orphan_files": orphan})
        inv.breakdown = [{"metric": "vault_documents", "value": doc_count},
                         {"metric": "physical_files", "value": len(phys)},
                         {"metric": "missing_files", "value": missing},
                         {"metric": "orphan_files", "value": orphan},
                         {"metric": "linked_people", "value": people},
                         {"metric": "linked_households", "value": households}]
        inv.readiness = "ready"
        if missing:
            inv.reasons.append(f"{missing} vault documents have no file on disk")
        if orphan:
            inv.reasons.append(f"{orphan} files on disk are not referenced by any vault document")
    except Exception as exc:  # noqa: BLE001 — inventory must never fail on DB access
        inv.metadata["db_error"] = str(exc)
        inv.readiness = "db-unavailable"
        inv.reasons.append(f"vault DB read unavailable: {exc}")
    return inv


# --- Drake documents (read-only, client artifacts ONLY — never the program/backup) --------------

# Program / application / data files — NEVER client artifacts (excluded from candidate counts).
_DRAKE_PROGRAM_EXTS = frozenset({
    ".exe", ".dll", ".msi", ".msp", ".sys", ".ocx", ".com", ".bat", ".cmd", ".ps1", ".vbs", ".scr",
    ".cab", ".tmp", ".lock", ".ini", ".cfg", ".config", ".dat", ".idx", ".dbf", ".mdb", ".accdb",
    ".fdb", ".db", ".sqlite", ".log", ".bak"})
# Directory-name keywords that mark PROGRAM content (installers, updates, engine, DB, fonts, …).
_DRAKE_PROGRAM_DIRS = ("servpack", "setup", "update", "install", "drivers", "help", "template",
                       "fonts", "system", "\\bin", "backupdb", "ef database", "program files")
# Directory-name keywords that mark CLIENT-ARTIFACT areas.
_DRAKE_CANDIDATE_DIRS = ("return", "print", "document", "report", "ack", "acknowledg", "efile",
                         "e-file", "filing", "evidence", "client")


def _drake_year(name: str) -> str:
    import re
    m = re.search(r"drake(\d{2})", name.lower())
    return f"20{m.group(1)}" if m else "unknown"


def _classify_drake_file(fp: str, ext: str) -> tuple[str, str]:
    """Return (kind, label): kind ∈ {candidate, excluded, other}. Conservative: only clearly client
    artifacts are candidates; program/DB/app files are excluded; everything else is uncounted 'other'."""
    parts = [p.lower() for p in Path(fp).parts]
    blob = " ".join(parts)
    if ext in _DRAKE_PROGRAM_EXTS:
        return ("excluded", "program_or_data_file")
    if any(k in seg for seg in parts for k in _DRAKE_PROGRAM_DIRS) or any(k in blob for k in _DRAKE_PROGRAM_DIRS):
        return ("excluded", "program_directory")
    if ext == ".pdf":
        if any(k in blob for k in ("ack", "acknowledg", "efile", "e-file")):
            return ("candidate", "acknowledgement")
        if "filing" in blob:
            return ("candidate", "filing_history")
        if "report" in blob:
            return ("candidate", "report")
        if any(k in blob for k in ("client", "document")):
            return ("candidate", "client_document")
        if any(k in blob for k in ("return", "print")):
            return ("candidate", "return_pdf")
        return ("candidate", "return_pdf")            # a bare PDF in a Drake tree is most likely a printed return
    if ext in (".txt", ".csv", ".xml", ".zip") and any(
            k in blob for k in ("ack", "acknowledg", "efile", "e-file", "filing", "evidence")):
        return ("candidate", "filing_evidence")
    return ("other", "unclassified")


def inventory_drake(cfg: MigrationConfig) -> SourceInventory:
    """Read-only Drake census of the explicitly configured roots ONLY. Counts CLIENT artifacts (tax
    return PDFs, acknowledgements, filing history/evidence, reports, client docs) by year + type;
    excludes installers/executables/DBs/SERVPACK/app files. Counts only — never imports or indexes."""
    roots = list(cfg.drake_roots)
    present = [r for r in roots if r.exists()]
    inv = SourceInventory(source="Drake", available=bool(present),
                          path="; ".join(str(r) for r in present)[:1000])
    by: dict[tuple[str, str], list[int]] = {}
    excluded: dict[str, int] = {}
    other = [0, 0]
    sample_candidates: list[dict] = []
    sample_excluded: list[dict] = []
    for root in present:
        year = _drake_year(root.name)
        for dp, _dn, fn in os.walk(root):
            for name in fn:
                fp = os.path.join(dp, name)
                ext = os.path.splitext(name)[1].lower()
                try:
                    size = os.path.getsize(fp)
                except OSError:
                    inv.unreadable_files += 1
                    continue
                kind, label = _classify_drake_file(fp, ext)
                if kind == "candidate":
                    e = by.setdefault((year, label), [0, 0])
                    e[0] += 1
                    e[1] += size
                    if len(sample_candidates) < 15:
                        sample_candidates.append({"path": os.path.relpath(fp, root), "type": label, "year": year})
                elif kind == "excluded":
                    excluded[label] = excluded.get(label, 0) + 1
                    if len(sample_excluded) < 15:
                        sample_excluded.append({"path": os.path.relpath(fp, root), "category": label})
                else:
                    other[0] += 1
                    other[1] += size
    cand_count = sum(v[0] for v in by.values())
    cand_bytes = sum(v[1] for v in by.values())
    inv.document_count = cand_count
    inv.total_bytes = cand_bytes                       # candidate (client-artifact) bytes only
    inv.object_counts.update({"files": cand_count, "candidate_documents": cand_count,
                              "excluded_program_files": sum(excluded.values()), "other_uncounted": other[0]})
    inv.metadata.update({"roots_scanned": [str(r) for r in present],
                         "excluded_by_category": excluded,
                         "sample_candidates": sample_candidates, "sample_excluded": sample_excluded,
                         "filtering_rules": {
                             "candidate_extensions": [".pdf", "+.txt/.csv/.xml/.zip in ack/efile/filing dirs"],
                             "candidate_dir_keywords": list(_DRAKE_CANDIDATE_DIRS),
                             "excluded_extensions": sorted(_DRAKE_PROGRAM_EXTS),
                             "excluded_dir_keywords": list(_DRAKE_PROGRAM_DIRS)}})
    inv.breakdown = [{"year": y, "artifact_type": t, "candidate_count": v[0], "candidate_bytes": v[1]}
                     for (y, t), v in sorted(by.items())]
    inv.readiness = "ready" if present else "unavailable"
    if not present:
        inv.reasons.append("no configured Drake roots exist on this host")
    inv.reasons.append("Client artifacts COUNTED for review only — NOT imported/indexed; program/DB/app "
                       "files and the AWS/Drake backup at large are excluded.")
    return inv


# --- Unclassified legacy trees (needs review) --------------------------------

_SYSTEM_DIRS = frozenset({"windows", "program files", "program files (x86)", "programdata",
                          "$recycle.bin", "system volume information", "recovery", "perflogs",
                          "intel", "$winreagent", "config.msi"})


def _assigned_roots(cfg: MigrationConfig) -> list[str]:
    roots = [cfg.wealthbox_export, cfg.taxdome_root, cfg.sharepoint_root, cfg.scanner_root,
             cfg.document_root, cfg.vault_root, *cfg.drake_roots]
    return [str(r).replace("/", "\\").lower().rstrip("\\") for r in roots]


def inventory_unclassified(cfg: MigrationConfig) -> SourceInventory:
    """Sweep the configured search roots' TOP-LEVEL folders for substantial document trees NOT already
    assigned to a known source (and not excluded / system). Flags them 'needs review' — never imports."""
    assigned = _assigned_roots(cfg)
    present = [r for r in cfg.unclassified_search_roots if r.exists()]
    inv = SourceInventory(source="Unclassified (needs review)", available=bool(present),
                          path="; ".join(str(r) for r in cfg.unclassified_search_roots))
    candidates: list[dict] = []
    for sroot in present:
        try:
            entries = list(os.scandir(sroot))
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir():
                continue
            full = entry.path
            low = full.replace("/", "\\").lower().rstrip("\\")
            if is_excluded(full) or entry.name.lower() in _SYSTEM_DIRS:
                continue
            if any(low == a or low.startswith(a + "\\") or a.startswith(low + "\\") for a in assigned):
                continue
            s = _scan_tree(Path(full))
            if s["documents"] >= 25:
                candidates.append({"path": full, "documents": s["documents"], "files": s["files"],
                                   "bytes": s["bytes"]})
    inv.document_count = sum(c["documents"] for c in candidates)
    inv.total_bytes = sum(c["bytes"] for c in candidates)
    inv.object_counts.update({"needs_review_trees": len(candidates),
                              "files": sum(c["files"] for c in candidates)})
    inv.breakdown = [{"path": c["path"], "documents": c["documents"], "files": c["files"],
                      "estimated_gb": round(c["bytes"] / (1024 ** 3), 2), "classification": "needs review"}
                     for c in sorted(candidates, key=lambda x: -x["documents"])]
    inv.readiness = ("needs-review" if candidates else "empty") if present else "unavailable"
    inv.reasons.append("Substantial unassigned document trees flagged for human review — NOT imported. "
                       "Set CLIENT360_UNCLASSIFIED_SEARCH_ROOTS to control the sweep scope.")
    return inv


ALL_PROVIDERS = {
    "wealthbox": inventory_wealthbox,
    "taxdome": inventory_taxdome,
    "sharepoint": inventory_sharepoint,
    "scanner": inventory_scanner,
    "existing_docs": inventory_existing_docs,
    "vault": inventory_vault,
    "drake": inventory_drake,
    "unclassified": inventory_unclassified,
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
            # per-source detail rows (Drake by year/type, Vault metrics, unclassified trees)
            for row in inv.breakdown:
                recon.append({"source": f"{inv.source} · detail", **row})
            if inv.readiness in {"unavailable", "needs-export", "db-unavailable"}:
                exceptions.append({"source": inv.source, "readiness": inv.readiness,
                                   "path": inv.path, "reason": "; ".join(inv.reasons)})
            if inv.unreadable_files:
                exceptions.append({"source": inv.source, "readiness": "unreadable-files",
                                   "path": inv.path, "reason": f"{inv.unreadable_files} unreadable files"})
            # Vault integrity issues surface as exceptions for review
            if inv.object_counts.get("missing_files"):
                exceptions.append({"source": inv.source, "readiness": "missing-files", "path": inv.path,
                                   "reason": f"{inv.object_counts['missing_files']} vault docs missing their file"})
            if inv.object_counts.get("orphan_files"):
                exceptions.append({"source": inv.source, "readiness": "orphan-files", "path": inv.path,
                                   "reason": f"{inv.object_counts['orphan_files']} orphan files not referenced by any vault doc"})
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
