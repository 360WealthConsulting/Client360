"""TaxDome Document Migration — the permanent, one-time TaxDome retirement pipeline.

This is a MIGRATION, not a sync/connector. Phase 2 delivers PREVIEW only (read-only): it walks the
confirmed local TaxDome document repository, matches every top-level client folder to a canonical
Client360 person / household / organization (reusing the proven ``taxdome_drive`` exact-name matching),
classifies each folder matched / ambiguous / unmatched, computes the PROPOSED destination path for each
matched file (carrying the stable canonical id), and detects OneDrive placeholders, zero-byte files,
unreadable files, duplicate-content candidates, and destination collisions.

Destination convention (matched only):
    <dest root>\\Clients\\<canonical-id> - <display name>\\Tax\\<year|Undated>\\<preserved subpath>\\<file>

Guardrails: PREVIEW makes ZERO database writes, copies/moves NO files, and modifies NO existing data.
Ambiguous/unmatched folders stay preview-only exceptions in the human-review queue — no unlinked document
rows are proposed and nothing is copied. APPLY is refused up front until this preview is approved.
Writes ``migration_preview.csv`` / ``migration_summary.txt`` / ``migration_manifest.json`` (+ framework artifacts).
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
from pathlib import Path

from app.importers.taxdome_drive import _folder_person_keys, _is_ignored_file, _name_key
from app.services.migration.base import MigrationJob, Mode, Outcome

_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
# OneDrive / Files-On-Demand cloud-only attribute bits (Windows only).
_FILE_ATTRIBUTE_OFFLINE = 0x1000
_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
_PLACEHOLDER_MASK = _FILE_ATTRIBUTE_OFFLINE | _FILE_ATTRIBUTE_RECALL_ON_OPEN | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS


def _is_placeholder(st) -> bool:
    """True if a stat result marks a OneDrive cloud-only placeholder (Windows). Elsewhere → False."""
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & _PLACEHOLDER_MASK)


def _year_and_subpath(dir_parts: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """Detect the tax year (first path segment that is a 19xx/20xx) and the preserved subpath beneath it.
    No reliable year → ('Undated', all dir parts preserved)."""
    if dir_parts and _YEAR_RE.match(dir_parts[0]):
        return dir_parts[0], dir_parts[1:]
    return "Undated", dir_parts


def _load_directory(conn):
    """Read-only: name_key -> [person rows]; household id -> name. One query each."""
    from sqlalchemy import select

    from app.db import households, people
    idx: dict[str, list[dict]] = {}
    for r in conn.execute(select(people.c.id, people.c.first_name, people.c.last_name,
                                 people.c.full_name, people.c.household_id)).mappings():
        key = _name_key(r["full_name"])
        if key:
            idx.setdefault(key, []).append(dict(r))
    hh = {r["id"]: r["name"] for r in conn.execute(select(households.c.id, households.c.name)).mappings()}
    return idx, hh


def _person_display(p: dict) -> str:
    last, first = (p.get("last_name") or "").strip(), (p.get("first_name") or "").strip()
    return f"{last}, {first}".strip(", ") or (p.get("full_name") or f"Person {p['id']}")


def _classify_folder(folder_name: str, idx: dict[str, list[dict]], hh: dict[int, str]) -> dict:
    """matched / ambiguous / unmatched, with the matched entity type + canonical id + display name."""
    base = {"status": "unmatched", "reason": "no matching canonical person", "entity_type": "",
            "canonical_id": None, "display_name": "", "candidates": []}
    keys = _folder_person_keys(folder_name)
    if not keys:
        return {**base, "reason": "no parseable name"}
    per_key = {k: idx.get(k, []) for k in keys}
    if any(len(v) > 1 for v in per_key.values()):
        cand = sorted({c["full_name"] for v in per_key.values() for c in v})
        return {**base, "status": "ambiguous", "reason": "a name matches multiple people",
                "candidates": cand[:8]}
    matched = [v[0] for v in per_key.values() if len(v) == 1]
    if not matched:
        return base
    unique = {m["id"] for m in matched}
    households = {m["household_id"] for m in matched if m["household_id"] is not None}
    if len(keys) == 1 and len(unique) == 1:
        p = matched[0]
        return {"status": "matched", "reason": "unique person", "entity_type": "person",
                "canonical_id": p["id"], "display_name": _person_display(p), "candidates": [p["full_name"]]}
    if len(households) == 1:
        hid = next(iter(households))
        return {"status": "matched", "reason": "joint -> shared household", "entity_type": "household",
                "canonical_id": hid, "display_name": hh.get(hid) or f"Household {hid}",
                "candidates": [m["full_name"] for m in matched]}
    if len(unique) == 1:
        p = matched[0]
        return {"status": "matched", "reason": "single distinct person", "entity_type": "person",
                "canonical_id": p["id"], "display_name": _person_display(p), "candidates": [p["full_name"]]}
    return {**base, "status": "ambiguous", "reason": "matched people without one common household",
            "candidates": [m["full_name"] for m in matched]}


def _sanitize(component: str) -> str:
    """Make a path component safe for a destination folder name."""
    return re.sub(r'[<>:"/\\|?*]', "_", component).strip().rstrip(".") or "_"


class TaxDomeDocumentMigration(MigrationJob):
    source_system = "TaxDome Documents"
    # Phase 2: PREVIEW only. apply/reconcile/rollback refused up front until approved.
    supported_modes = frozenset({Mode.INVENTORY, Mode.PREVIEW})

    def _preview(self, **_opts) -> Outcome:
        root = self.config.taxdome_migration_root
        dest_root = self.config.migration_dest_root
        if not root.exists():
            return Outcome(counts={"top_level_folders": 0},
                           exceptions=[{"reason": f"TaxDome migration source not found: {root}"}],
                           notes=[f"Source not found: {root}"])

        idx: dict[str, list[dict]] = {}
        hh: dict[int, str] = {}
        db_note = None
        try:
            from app.db import engine
            with engine.connect() as conn:
                idx, hh = _load_directory(conn)
        except Exception as exc:  # noqa: BLE001 — preview must not fail on DB access
            db_note = f"canonical directory unavailable ({exc}); folders reported unmatched"

        rows: list[dict] = []
        name_size_seen: dict[tuple[str, int], int] = {}     # duplicate-content candidates (name+size)
        dest_seen: dict[str, int] = {}                       # destination collisions
        agg = {"files": 0, "folders": 0, "bytes": 0, "zero_byte": 0, "unreadable": 0,
               "ignored": 0, "placeholders": 0}
        by_status = {"matched": 0, "ambiguous": 0, "unmatched": 0}
        by_readiness = {"ready": 0, "review-required": 0, "blocked": 0}
        est_rows = {"matched": 0, "ambiguous": 0, "unmatched": 0}

        top_level = sorted((e for e in os.scandir(root) if e.is_dir()), key=lambda e: e.name.lower())
        for entry in top_level:
            folder = entry.name
            cls = _classify_folder(folder, idx, hh) if idx else {
                "status": "unmatched", "reason": db_note or "no canonical directory", "entity_type": "",
                "canonical_id": None, "display_name": "", "candidates": []}
            matched = cls["status"] == "matched"
            dest_folder = (f"{cls['canonical_id']} - {_sanitize(cls['display_name'])}" if matched else "")
            proposed_root = (str(dest_root / "Clients" / dest_folder) if matched else "(review queue)")

            f_files = f_folders = f_bytes = f_zero = f_unread = f_ignored = f_ph = f_coll = f_docrows = 0
            client_path = Path(entry.path)
            for dp, dn, fn in os.walk(entry.path):
                f_folders += len(dn)
                for name in fn:
                    f_files += 1
                    if _is_ignored_file(name):
                        f_ignored += 1
                        continue
                    fp = os.path.join(dp, name)
                    try:
                        st = os.stat(fp)
                    except OSError:
                        f_unread += 1
                        continue
                    size = st.st_size
                    f_bytes += size
                    f_docrows += 1
                    if size == 0:
                        f_zero += 1
                    if _is_placeholder(st):
                        f_ph += 1
                    name_size_seen[(name.lower(), size)] = name_size_seen.get((name.lower(), size), 0) + 1
                    if matched:
                        rel = Path(fp).relative_to(client_path)
                        year, sub = _year_and_subpath(rel.parts[:-1])
                        dest = str(dest_root / "Clients" / dest_folder / "Tax" / year / Path(*sub) / name)
                        if dest_seen.get(dest):
                            f_coll += 1
                        dest_seen[dest] = dest_seen.get(dest, 0) + 1

            # readiness
            if not matched:
                readiness = "review-required"
            elif f_ph:
                readiness = "blocked"                        # cloud-only files cannot be copied until hydrated
            elif f_unread or f_coll:
                readiness = "review-required"
            else:
                readiness = "ready"

            by_status[cls["status"]] += 1
            by_readiness[readiness] += 1
            est_rows[cls["status"]] += f_docrows
            for k, v in (("files", f_files), ("folders", f_folders), ("bytes", f_bytes),
                         ("zero_byte", f_zero), ("unreadable", f_unread), ("ignored", f_ignored),
                         ("placeholders", f_ph)):
                agg[k] += v

            rows.append({
                "source_folder": folder, "classification": cls["status"], "match_reason": cls["reason"],
                "entity_type": cls["entity_type"], "canonical_id": cls["canonical_id"] or "",
                "display_name": cls["display_name"], "proposed_destination_root": proposed_root,
                "files": f_files, "folders": f_folders, "bytes": f_bytes,
                "estimated_document_rows": f_docrows,
                "duplicate_candidate_hint": "yes" if f_files > 1 else "",
                "collision_count": f_coll, "placeholder_count": f_ph,
                "zero_byte_count": f_zero, "unreadable_count": f_unread, "ignored_count": f_ignored,
                "candidate_names": "; ".join(cls["candidates"]), "readiness": readiness,
            })

        dup_groups = sum(1 for n in name_size_seen.values() if n > 1)
        dup_files = sum(n - 1 for n in name_size_seen.values() if n > 1)
        collisions = sum(v - 1 for v in dest_seen.values() if v > 1)

        # destination pre-flight (read-only stat; never writes to D:\)
        preflight = self._dest_preflight(dest_root, agg["bytes"])

        counts = {
            "source_root": str(root), "destination_root": str(dest_root),
            "top_level_folders": len(top_level),
            "matched_folders": by_status["matched"], "ambiguous_folders": by_status["ambiguous"],
            "unmatched_folders": by_status["unmatched"],
            "ready_folders": by_readiness["ready"], "review_required_folders": by_readiness["review-required"],
            "blocked_folders": by_readiness["blocked"],
            "total_files": agg["files"], "total_folders": agg["folders"],
            "total_bytes": agg["bytes"], "estimated_gb": round(agg["bytes"] / (1024 ** 3), 2),
            "ignored_files": agg["ignored"], "zero_byte_files": agg["zero_byte"],
            "unreadable_files": agg["unreadable"], "cloud_only_placeholders": agg["placeholders"],
            "duplicate_candidate_groups": dup_groups, "duplicate_candidate_files": dup_files,
            "destination_collisions": collisions,
            "estimated_document_rows_total": est_rows["matched"] + est_rows["ambiguous"] + est_rows["unmatched"],
            "estimated_document_rows_matched": est_rows["matched"],
            "estimated_document_rows_ambiguous_review": est_rows["ambiguous"],
            "estimated_document_rows_unmatched_review": est_rows["unmatched"],
            **preflight,
        }
        exceptions = [{"source_folder": r["source_folder"], "classification": r["classification"],
                       "readiness": r["readiness"], "reason": r["match_reason"],
                       "placeholder_count": r["placeholder_count"], "collision_count": r["collision_count"],
                       "candidate_names": r["candidate_names"]}
                      for r in rows if r["readiness"] != "ready"]
        notes = [
            "PREVIEW ONLY — no database rows, no files copied/moved, no existing data modified.",
            "One-time TaxDome retirement pipeline (no sync/connector). APPLY disabled until approved.",
            "Ambiguous/unmatched folders are review-only exceptions: no unlinked document rows proposed, "
            "nothing copied — they stay in the human-review queue until linked (MDM, no bulk auto-merge).",
            "cloud_only_placeholders>0 marks folders BLOCKED — those files must be hydrated before apply.",
        ]
        if db_note:
            notes.append(db_note)

        self._write_named_artifacts(rows, counts, notes)
        return Outcome(counts=counts, exceptions=exceptions, reconciliation=rows, notes=notes)

    @staticmethod
    def _dest_preflight(dest_root: Path, source_bytes: int) -> dict:
        drive = Path(dest_root.anchor or dest_root)
        out = {"dest_root_exists": dest_root.exists(), "dest_drive": str(drive),
               "dest_drive_exists": drive.exists()}
        try:
            usage = shutil.disk_usage(str(drive if drive.exists() else dest_root))
            out["dest_free_gb"] = round(usage.free / (1024 ** 3), 2)
            out["source_gb"] = round(source_bytes / (1024 ** 3), 2)
            out["fits_with_10pct_margin"] = usage.free > source_bytes * 1.1
        except OSError:
            out["dest_free_gb"] = None
            out["fits_with_10pct_margin"] = None
        return out

    def _write_named_artifacts(self, rows: list[dict], counts: dict, notes: list[str]) -> None:
        run_dir = getattr(self, "_last_run_dir", None)
        if run_dir is None:
            return
        run_dir = Path(run_dir)
        fields = list(rows[0].keys()) if rows else ["source_folder"]
        with (run_dir / "migration_preview.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        (run_dir / "migration_manifest.json").write_text(
            json.dumps({"source_system": self.source_system, "mode": "preview",
                        "counts": counts, "notes": notes}, indent=2, default=str), encoding="utf-8")
        lines = [
            "TaxDome Document Migration — PREVIEW (read-only)",
            f"source      : {counts.get('source_root')}",
            f"destination : {counts.get('destination_root')}",
            f"  D: drive exists: {counts.get('dest_drive_exists')}   dest folder exists: {counts.get('dest_root_exists')}",
            f"  free: {counts.get('dest_free_gb')} GB   source: {counts.get('source_gb', counts.get('estimated_gb'))} GB"
            f"   fits(+10%): {counts.get('fits_with_10pct_margin')}",
            "",
            f"top-level client folders : {counts.get('top_level_folders')}",
            f"  matched   : {counts.get('matched_folders')}   ambiguous: {counts.get('ambiguous_folders')}"
            f"   unmatched: {counts.get('unmatched_folders')}",
            f"  readiness  ready: {counts.get('ready_folders')}   review-required: "
            f"{counts.get('review_required_folders')}   blocked: {counts.get('blocked_folders')}",
            "",
            f"files: {counts.get('total_files')}   folders: {counts.get('total_folders')}   "
            f"size: {counts.get('estimated_gb')} GB",
            f"ignored: {counts.get('ignored_files')}   zero-byte: {counts.get('zero_byte_files')}   "
            f"unreadable: {counts.get('unreadable_files')}   cloud-only placeholders: {counts.get('cloud_only_placeholders')}",
            f"duplicate candidates: {counts.get('duplicate_candidate_groups')} groups / "
            f"{counts.get('duplicate_candidate_files')} files   destination collisions: {counts.get('destination_collisions')}",
            "",
            f"estimated document rows — total {counts.get('estimated_document_rows_total')} | "
            f"matched(link) {counts.get('estimated_document_rows_matched')} | "
            f"ambiguous(review) {counts.get('estimated_document_rows_ambiguous_review')} | "
            f"unmatched(review) {counts.get('estimated_document_rows_unmatched_review')}",
            "",
            *notes,
        ]
        (run_dir / "migration_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
