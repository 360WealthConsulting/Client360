"""Stage B CLI — re-own proven joint personal-return documents to their canonical household (guarded).

PREVIEW is read-only. APPLY is guarded (``--confirm`` + verified non-empty ``--backup`` + pinned
``--expect`` count, fail-closed on drift) and changes DOCUMENT OWNERSHIP ONLY (person -> household) — it
never changes storage_uri/document_sources and never moves a file.

Usage::
    python -m scripts.migration.reown_joint_documents --preview
    python -m scripts.migration.reown_joint_documents --apply --confirm \
        --backup D:\\Client360\\SQLBackups\\pre-stageB.dump --expect <N>
"""
from __future__ import annotations

import argparse
import csv
import sys

from app.services.migration.joint_document_reownership import (
    ReownershipGuardError,
    apply,
    preview,
)

_FIELDS = ["document_id", "current_person_id", "proposed_household_id", "evidence",
           "current_storage_uri", "proposed_destination", "relocation_required", "original_name"]


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python -m scripts.migration.reown_joint_documents",
                                description="Re-own proven joint personal-return documents to households.")
    p.add_argument("--preview", action="store_true", help="Read-only preview.")
    p.add_argument("--apply", action="store_true", help="Guarded APPLY (needs --confirm/--backup/--expect).")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--backup", default=None)
    p.add_argument("--expect", type=int, default=None)
    p.add_argument("--out", default=None, help="Write the re-ownable reconciliation CSV here (preview).")
    args = p.parse_args(argv)

    if args.apply:
        try:
            res = apply(confirm=args.confirm, backup=args.backup, expect=args.expect)
        except ReownershipGuardError as exc:
            print(f"GUARD ABORT: {exc}")
            return 3
        print("=== Stage B APPLY complete (document ownership only) ===")
        for k in ("reownable", "reowned", "already_applied"):
            print(f"  {k}: {res[k]}")
        if res["skipped_conflicts"]:
            print(f"  skipped_conflicts: {len(res['skipped_conflicts'])}")
            for did, why in res["skipped_conflicts"][:20]:
                print(f"    - doc {did}: {why}")
        print("\nNo storage_uri/document_sources changed; no files moved.")
        return 0

    res = preview()
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=_FIELDS)
            w.writeheader()
            for r in res["reownable_rows"]:
                w.writerow({k: r.get(k, "") for k in _FIELDS})
    print("=== Stage B PREVIEW (read-only) ===")
    print(f"  candidate person-owned docs: {res['candidates']}")
    print(f"  reownable (proven joint personal returns): {res['reownable']}")
    print(f"  excluded: {res['excluded']}")
    print("  exclusions by reason:")
    for reason, n in sorted(res["exclusions_by_reason"].items()):
        print(f"      {reason}: {n}")
    if args.out:
        print(f"\nreconciliation written: {args.out}")
    print("\nPreview is read-only. No ownership/storage/document_sources changes; no files moved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
