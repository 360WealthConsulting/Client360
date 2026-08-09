"""Stage C CLI — relocate Stage-B household-owned documents to their household destination (guarded).

PREVIEW is read-only. APPLY delegates to the production repository-relocation engine (copy -> verify
SHA-256+size -> repoint storage_uri only after the destination verifies; source bytes RETAINED;
document_sources preserved) scoped to household-owned re-owned documents, guarded by ``--confirm`` +
verified non-empty ``--backup`` + pinned ``--expect``. It changes no ownership and creates no rows.

Usage::
    python -m scripts.migration.relocate_household_documents --preview
    python -m scripts.migration.relocate_household_documents --apply --confirm \
        --backup D:\\Client360\\SQLBackups\\pre-stageC.dump --expect <N>
"""
from __future__ import annotations

import argparse
import csv
import sys

from app.services.migration.canonical_repair import RepairGuardError
from app.services.migration.household_relocation import apply, preview

_FIELDS = ["document_id", "state", "area", "entity", "category", "year", "filename",
           "current_storage_uri", "proposed_destination", "size_bytes", "source_system"]


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.relocate_household_documents",
                                description="Relocate Stage-B household-owned documents (guarded).")
    p.add_argument("--preview", action="store_true", help="Read-only preview.")
    p.add_argument("--apply", action="store_true", help="Guarded APPLY (needs --confirm/--backup/--expect).")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--backup", default=None)
    p.add_argument("--expect", type=int, default=None)
    p.add_argument("--out", default=None, help="Write the preview reconciliation CSV here.")
    args = p.parse_args(argv)

    if args.apply:
        try:
            res = apply(confirm=args.confirm, backup=args.backup, expect=args.expect)
        except RepairGuardError as exc:
            print(f"GUARD ABORT: {exc}")
            return 3
        c = res["counts"]
        print("=== Stage C APPLY complete (copy -> verify -> repoint; source retained) ===")
        print(f"  relocated (copied+verified+repointed): {c.get('rows_inserted')}")
        print(f"  skipped_already_relocated: {c.get('skipped_already_relocated')}")
        print(f"  total (Households): {c.get('Households')}")
        print("\nNo ownership changed, no document_sources changed, source files retained.")
        return 0

    res = preview()
    c = res["counts"]
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=_FIELDS)
            w.writeheader()
            for r in res["rows"]:
                w.writerow({k: r.get(k, "") for k in _FIELDS})
    print("=== Stage C PREVIEW (read-only) ===")
    print(f"  household_scope_total: {c['household_scope_total']}")
    print(f"  needs_relocation: {c['needs_relocation']}")
    print(f"  already_in_repository: {c['already_in_repository']}")
    print(f"  missing_source: {c['missing_source']}")
    print(f"  cloud_only_placeholders: {c['cloud_only_placeholders']}")
    print(f"  destination_collisions: {c['destination_collisions']}")
    print(f"  relocatable_bytes: {c['relocatable_bytes']} ({c['relocatable_gb']} GB)")
    print(f"  destination_root: {c['destination_root']}")
    if args.out:
        print(f"\nreconciliation written: {args.out}")
    print("\nPreview is read-only. No bytes copied, no storage_uri/document_sources changed, no files moved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
