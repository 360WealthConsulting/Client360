"""Wealthbox contacts + households — the first migration (PREVIEW ONLY in Phase 1).

``preview`` parses the raw Wealthbox export (the same ``*contacts*.zip`` / CSV shape the existing
``app.importers.wealthbox`` reads) and reports EXACTLY what an apply would create — candidate
``source_contacts`` rows, resolvable households, in-export duplicate indicators, and unusable/exception
rows — writing the standard four artifacts. It makes NO database writes and moves no files.

``apply`` is deliberately NOT enabled in Phase 1: it raises so the framework cannot import Wealthbox
data until the preview is reviewed and approved. Identity de-duplication remains the human-approved MDM
merge engine — never a bulk automatic merge here.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path

from app.importers.wealthbox import clean, normalize_email, normalize_phone
from app.services.migration.base import MigrationJob, Mode, Outcome
from app.services.migration.inventory import inventory_wealthbox

_HOUSEHOLD_KEYS = ("household", "household_title", "household_name", "household_id")


def _name_key(first: str | None, last: str | None, full: str | None) -> str | None:
    """Order-insensitive normalized name key (so 'Smith, John' == 'John Smith'). Conservative — used
    only as a duplicate *indicator*, never to auto-merge."""
    basis = " ".join(p for p in (first, last) if p) or (full or "")
    tokens = sorted(t for t in re.split(r"[^a-z0-9]+", basis.lower()) if t)
    return " ".join(tokens) or None


def _iter_rows(export_dir: Path):
    """Yield (source_file, row_dict) for every contact row in the export — from ``*contacts*.zip`` and
    any loose ``*.csv``. Read-only."""
    for zip_path in sorted(export_dir.glob("*contacts*.zip")):
        with zipfile.ZipFile(zip_path) as archive:
            for name in archive.namelist():
                if not name.lower().endswith(".csv"):
                    continue
                with archive.open(name) as binary:
                    reader = csv.DictReader(io.TextIOWrapper(binary, encoding="utf-8-sig",
                                                             errors="replace", newline=""))
                    for row in reader:
                        yield zip_path.name, row
    for csv_path in sorted(export_dir.glob("*.csv")):
        with csv_path.open(encoding="utf-8-sig", errors="replace", newline="") as f:
            for row in csv.DictReader(f):
                yield csv_path.name, row


def _household_of(row: dict) -> str | None:
    for k in _HOUSEHOLD_KEYS:
        v = clean(row.get(k))
        if v:
            return v
    return None


class WealthboxContactsMigration(MigrationJob):
    source_system = "Wealthbox"
    # Phase 1: read-only only. apply/reconcile/rollback are refused up front (before any DB access).
    supported_modes = frozenset({Mode.INVENTORY, Mode.PREVIEW})

    def _inventory(self, **_opts) -> Outcome:
        inv = inventory_wealthbox(self.config)
        return Outcome(
            counts={"available": inv.available, "readiness": inv.readiness,
                    **{k: v for k, v in inv.object_counts.items()}},
            exceptions=[] if inv.available else [{"source": "Wealthbox", "reason": "; ".join(inv.reasons)}],
            reconciliation=[{"source": "Wealthbox", "path": inv.path, "readiness": inv.readiness,
                             "reasons": "; ".join(inv.reasons)}],
            notes=["Inventory only — no writes."])

    def _preview(self, **_opts) -> Outcome:
        export_dir = self.config.wealthbox_export
        if not export_dir.exists():
            return Outcome(
                counts={"rows_read": 0},
                exceptions=[{"reason": f"Wealthbox export not found at {export_dir}"}],
                notes=[f"No export directory: {export_dir}. Run the read-only export (E1) first."])

        rows_read = 0
        hashes: set[str] = set()
        with_email = with_phone = with_name = unusable = missing_id = 0
        emails: dict[str, int] = {}
        phones: dict[str, int] = {}
        names: dict[str, int] = {}
        households: dict[str, int] = {}
        contacts_with_household = 0
        household_key_present = False
        per_file: dict[str, int] = {}
        exceptions: list[dict] = []

        for source_file, row in _iter_rows(export_dir):
            rows_read += 1
            per_file[source_file] = per_file.get(source_file, 0) + 1
            sid = clean(row.get("external_unique_id")) or clean(row.get("id"))
            first, last = clean(row.get("first_name")), clean(row.get("last_name"))
            full = clean(row.get("name"))
            email = normalize_email(clean(row.get("primary_email")))
            phone = normalize_phone(clean(row.get("primary_phone")))
            nkey = _name_key(first, last, full)

            # a would-create source_contacts row is keyed by the same hash basis the importer uses
            hashes.add(sid or f"row:{source_file}:{rows_read}")
            if email:
                with_email += 1
                emails[email] = emails.get(email, 0) + 1
            if phone:
                with_phone += 1
                phones[phone] = phones.get(phone, 0) + 1
            if first or last or full:
                with_name += 1
            if nkey:
                names[nkey] = names.get(nkey, 0) + 1
            if not sid:
                missing_id += 1
            if not (first or last or full or email or phone):
                unusable += 1
                exceptions.append({"source_file": source_file, "row": rows_read,
                                   "reason": "unusable shell — no name, email, or phone"})

            hh = _household_of(row)
            if any(k in row for k in _HOUSEHOLD_KEYS):
                household_key_present = True
            if hh:
                contacts_with_household += 1
                households[hh] = households.get(hh, 0) + 1

        dup_email = sum(1 for n in emails.values() if n > 1)
        dup_phone = sum(1 for n in phones.values() if n > 1)
        dup_name = sum(1 for n in names.values() if n > 1)

        counts = {
            "rows_read": rows_read,
            "would_create_source_contacts": len(hashes),
            "with_name": with_name, "with_email": with_email, "with_phone": with_phone,
            "unusable_shells": unusable, "missing_source_id": missing_id,
            "households_detected": len(households), "contacts_with_household": contacts_with_household,
            "in_export_duplicate_email_groups": dup_email,
            "in_export_duplicate_phone_groups": dup_phone,
            "in_export_duplicate_name_groups": dup_name,
            "export_files": len(per_file),
        }
        recon = [{"source_file": f, "rows": n} for f, n in sorted(per_file.items())]
        recon.append({"source_file": "TOTAL", "rows": rows_read})
        notes = [
            "PREVIEW ONLY — no source_contacts written, no people/households created, no merges.",
            "Would-create count is candidate source_contacts rows; canonical identity resolution + "
            "de-duplication is the separate human-approved MDM merge step.",
        ]
        if not household_key_present:
            notes.append("No household column in this contacts export — households must come from the "
                         "Wealthbox households API/export; reported households_detected=0 here.")
        return Outcome(counts=counts, exceptions=exceptions, reconciliation=recon, notes=notes)

    def _apply(self, **_opts) -> Outcome:
        raise NotImplementedError(
            "Wealthbox apply is PREVIEW-ONLY in Phase 1. Apply is enabled only after the preview is "
            "reviewed and approved (and never performs bulk automatic person merges).")
