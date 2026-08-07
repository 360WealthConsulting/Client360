"""TaxDome Document Migration — the permanent, one-time TaxDome retirement pipeline.

This is a MIGRATION, not a sync/connector. Phase 2 delivers PREVIEW only (read-only): it walks the
confirmed local TaxDome document repository, matches every top-level client folder to canonical
Client360 people/households (reusing the proven ``taxdome_drive`` name-matching), classifies each folder
as matched / ambiguous / unmatched, and reports files / folders / bytes / duplicates / estimated
document rows. It writes ``migration_preview.csv``, ``migration_summary.txt``, ``migration_manifest.json``
(plus the framework's standard artifacts).

PREVIEW makes ZERO database writes, copies/moves NO files, and modifies NO existing data. APPLY is NOT
built yet — it is refused up front (before any DB access) until the preview is reviewed and approved.
There is no ongoing dependency on TaxDome: once APPLY runs and reconciles, TaxDome can be retired.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from app.importers.taxdome_drive import _folder_person_keys, _is_ignored_file, _name_key
from app.services.migration.base import MigrationJob, Mode, Outcome


def _scan_client_folder(root: Path) -> dict:
    """Read-only walk of one top-level client folder: files/folders/bytes, zero-byte, unreadable,
    ignored (thumbs.db/desktop.ini/~$/.tmp), estimated document rows (non-ignored files), and a
    name+size duplicate indicator. Enumeration only — never opens content, never hashes."""
    files = folders = total = zero = unreadable = ignored = 0
    seen: dict[tuple[str, int], int] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        folders += len(dirnames)
        for name in filenames:
            files += 1
            if _is_ignored_file(name):
                ignored += 1
                continue
            fp = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(fp)
            except OSError:
                unreadable += 1
                continue
            total += size
            if size == 0:
                zero += 1
            key = (name.lower(), size)
            seen[key] = seen.get(key, 0) + 1
    dup_groups = sum(1 for n in seen.values() if n > 1)
    dup_files = sum(n - 1 for n in seen.values() if n > 1)
    est_doc_rows = files - ignored
    return {"files": files, "folders": folders, "bytes": total, "zero_byte": zero,
            "unreadable": unreadable, "ignored": ignored, "estimated_document_rows": est_doc_rows,
            "duplicate_groups": dup_groups, "duplicate_files": dup_files}


def _people_index(conn) -> dict[str, list[dict]]:
    """name_key -> [{id, household_id, full_name}]. One read-only query; classification is in-memory
    (4,800+ folders must not each scan the whole people table)."""
    from sqlalchemy import select

    from app.db import people
    idx: dict[str, list[dict]] = {}
    for r in conn.execute(select(people.c.id, people.c.full_name, people.c.household_id)).mappings():
        key = _name_key(r["full_name"])
        if key:
            idx.setdefault(key, []).append({"id": r["id"], "household_id": r["household_id"],
                                            "full_name": r["full_name"]})
    return idx


def _classify_folder(folder_name: str, idx: dict[str, list[dict]]) -> dict:
    """Classify one top-level folder as matched / ambiguous / unmatched using the same rules as
    taxdome_drive.resolve_folder, but exposing the ambiguity distinction."""
    keys = _folder_person_keys(folder_name)
    if not keys:
        return {"status": "unmatched", "reason": "no parseable name", "person_id": None,
                "household_id": None, "candidates": []}
    per_key = {k: idx.get(k, []) for k in keys}
    if any(len(v) > 1 for v in per_key.values()):
        cand = sorted({c["full_name"] for v in per_key.values() for c in v})
        return {"status": "ambiguous", "reason": "a name matches multiple people",
                "person_id": None, "household_id": None, "candidates": cand[:8]}
    matched = [v[0] for v in per_key.values() if len(v) == 1]
    if not matched:
        return {"status": "unmatched", "reason": "no matching canonical person",
                "person_id": None, "household_id": None, "candidates": []}
    unique_people = {m["id"] for m in matched}
    households = {m["household_id"] for m in matched if m["household_id"] is not None}
    if len(keys) == 1 and len(unique_people) == 1:
        return {"status": "matched", "reason": "unique person", "person_id": matched[0]["id"],
                "household_id": None, "candidates": [matched[0]["full_name"]]}
    if len(households) == 1:
        return {"status": "matched", "reason": "joint -> shared household", "person_id": None,
                "household_id": next(iter(households)), "candidates": [m["full_name"] for m in matched]}
    if len(unique_people) == 1:
        return {"status": "matched", "reason": "single distinct person", "person_id": matched[0]["id"],
                "household_id": None, "candidates": [matched[0]["full_name"]]}
    return {"status": "ambiguous", "reason": "matched people without one common household",
            "person_id": None, "household_id": None, "candidates": [m["full_name"] for m in matched]}


class TaxDomeDocumentMigration(MigrationJob):
    source_system = "TaxDome Documents"
    # Phase 2: PREVIEW only. apply/reconcile/rollback refused up front until approved.
    supported_modes = frozenset({Mode.INVENTORY, Mode.PREVIEW})

    def _preview(self, **_opts) -> Outcome:
        root = self.config.taxdome_migration_root
        if not root.exists():
            return Outcome(counts={"top_level_folders": 0},
                           exceptions=[{"reason": f"TaxDome migration source not found: {root}"}],
                           notes=[f"Source not found: {root}"])

        # canonical people index (read-only)
        idx: dict[str, list[dict]] = {}
        db_note = None
        try:
            from app.db import engine
            with engine.connect() as conn:
                idx = _people_index(conn)
        except Exception as exc:  # noqa: BLE001 — preview must not fail on DB access
            db_note = f"people index unavailable ({exc}); all folders reported unmatched"

        rows: list[dict] = []
        global_seen: dict[tuple[str, int], int] = {}
        totals = {"matched": 0, "ambiguous": 0, "unmatched": 0}
        agg = {"files": 0, "folders": 0, "bytes": 0, "zero_byte": 0, "unreadable": 0,
               "ignored": 0, "estimated_document_rows": 0, "duplicate_groups": 0, "duplicate_files": 0}
        est_rows_by_status = {"matched": 0, "ambiguous": 0, "unmatched": 0}

        top_level = sorted((e for e in os.scandir(root) if e.is_dir()), key=lambda e: e.name.lower())
        for entry in top_level:
            folder = entry.name
            scan = _scan_client_folder(Path(entry.path))
            cls = _classify_folder(folder, idx) if idx else {
                "status": "unmatched", "reason": db_note or "no people index",
                "person_id": None, "household_id": None, "candidates": []}
            totals[cls["status"]] += 1
            for k in agg:
                agg[k] += scan[k]
            est_rows_by_status[cls["status"]] += scan["estimated_document_rows"]
            # global duplicate indicator across the whole tree
            for dp, _dn, fn in os.walk(entry.path):
                for name in fn:
                    if _is_ignored_file(name):
                        continue
                    try:
                        sz = os.path.getsize(os.path.join(dp, name))
                    except OSError:
                        continue
                    gk = (name.lower(), sz)
                    global_seen[gk] = global_seen.get(gk, 0) + 1
            rows.append({
                "top_level_folder": folder, "match_status": cls["status"], "match_reason": cls["reason"],
                "person_id": cls["person_id"] or "", "household_id": cls["household_id"] or "",
                "candidate_names": "; ".join(cls["candidates"]),
                "files": scan["files"], "folders": scan["folders"], "bytes": scan["bytes"],
                "ignored_files": scan["ignored"], "zero_byte_files": scan["zero_byte"],
                "unreadable_files": scan["unreadable"],
                "duplicate_groups": scan["duplicate_groups"], "duplicate_files": scan["duplicate_files"],
                "estimated_document_rows": scan["estimated_document_rows"],
            })

        global_dup_groups = sum(1 for n in global_seen.values() if n > 1)
        global_dup_files = sum(n - 1 for n in global_seen.values() if n > 1)
        counts = {
            "source_root": str(root),
            "top_level_folders": len(top_level),
            "matched_folders": totals["matched"], "ambiguous_folders": totals["ambiguous"],
            "unmatched_folders": totals["unmatched"],
            "total_files": agg["files"], "total_folders": agg["folders"],
            "total_bytes": agg["bytes"], "estimated_gb": round(agg["bytes"] / (1024 ** 3), 2),
            "ignored_files": agg["ignored"], "zero_byte_files": agg["zero_byte"],
            "unreadable_files": agg["unreadable"],
            "global_duplicate_groups": global_dup_groups, "global_duplicate_files": global_dup_files,
            "estimated_document_rows_total": agg["estimated_document_rows"],
            "estimated_document_rows_matched": est_rows_by_status["matched"],
            "estimated_document_rows_ambiguous": est_rows_by_status["ambiguous"],
            "estimated_document_rows_unmatched": est_rows_by_status["unmatched"],
        }
        exceptions = [{"top_level_folder": r["top_level_folder"], "match_status": r["match_status"],
                       "reason": r["match_reason"], "candidate_names": r["candidate_names"],
                       "estimated_document_rows": r["estimated_document_rows"]}
                      for r in rows if r["match_status"] in {"ambiguous", "unmatched"}]
        notes = [
            "PREVIEW ONLY — no database rows written, no files copied or moved, no existing data modified.",
            "One-time migration pipeline (no sync/connector). APPLY is disabled until this preview is approved.",
            "Matched = unique exact person, or joint -> one shared household (taxdome_drive rules). "
            "Ambiguous/unmatched folders are held for human review; MDM identity resolution is separate.",
        ]
        if db_note:
            notes.append(db_note)

        self._write_named_artifacts(rows, counts, notes)
        return Outcome(counts=counts, exceptions=exceptions,
                       reconciliation=rows, notes=notes)

    def _write_named_artifacts(self, rows: list[dict], counts: dict, notes: list[str]) -> None:
        """Write the exactly-named deliverables the migration requires, into this run's directory."""
        run_dir = getattr(self, "_last_run_dir", None)
        if run_dir is None:
            return
        run_dir = Path(run_dir)
        # migration_preview.csv — one row per top-level client folder
        fields = list(rows[0].keys()) if rows else ["top_level_folder"]
        with (run_dir / "migration_preview.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        # migration_manifest.json — full machine-readable summary
        (run_dir / "migration_manifest.json").write_text(
            json.dumps({"source_system": self.source_system, "mode": "preview",
                        "counts": counts, "notes": notes}, indent=2, default=str), encoding="utf-8")
        # migration_summary.txt — human summary
        lines = [
            "TaxDome Document Migration — PREVIEW (read-only)",
            f"source: {counts.get('source_root')}",
            "",
            f"top-level client folders : {counts.get('top_level_folders')}",
            f"  matched                : {counts.get('matched_folders')}",
            f"  ambiguous              : {counts.get('ambiguous_folders')}",
            f"  unmatched              : {counts.get('unmatched_folders')}",
            "",
            f"files    : {counts.get('total_files')}   folders: {counts.get('total_folders')}",
            f"size     : {counts.get('estimated_gb')} GB   ({counts.get('total_bytes')} bytes)",
            f"ignored  : {counts.get('ignored_files')}   zero-byte: {counts.get('zero_byte_files')}   "
            f"unreadable: {counts.get('unreadable_files')}",
            f"duplicates (name+size) : {counts.get('global_duplicate_groups')} groups / "
            f"{counts.get('global_duplicate_files')} extra files",
            "",
            f"estimated document rows (total)     : {counts.get('estimated_document_rows_total')}",
            f"  would link to a client (matched)  : {counts.get('estimated_document_rows_matched')}",
            f"  held for review (ambiguous)       : {counts.get('estimated_document_rows_ambiguous')}",
            f"  held for review (unmatched)       : {counts.get('estimated_document_rows_unmatched')}",
            "",
            *notes,
        ]
        (run_dir / "migration_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
