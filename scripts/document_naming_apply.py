"""Apply canonical document display names — SAFE preview rows only. DRY-RUN BY DEFAULT.

    python scripts/document_naming_apply.py                          # dry run over all SAFE rows
    python scripts/document_naming_apply.py --document 151 --document 152   # bounded, explicit ids
    python scripts/document_naming_apply.py --safe-all --apply       # write every SAFE row

Writes exactly one column, ``documents.display_name``. It never renames or moves a physical file,
never touches SharePoint/OneDrive, and never modifies ``original_name``, ``storage_path``,
``storage_uri``, ``sha256``, ownership or any other document field. The naming resolver is re-run
immediately before any write, so a stale preview cannot authorise one.
"""
from __future__ import annotations

import argparse

from app.security.models import Principal
from app.services.document_naming_apply import (
    APPLIED,
    CONFLICT_EXISTING_NAME,
    apply_display_names,
)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Apply SAFE document display names (dry run by default).")
    ap.add_argument("--document", action="append", type=int, default=None,
                    help="explicit document id (repeatable); bounded apply")
    ap.add_argument("--safe-all", action="store_true", help="every SAFE row in the live preview")
    ap.add_argument("--apply", action="store_true", help="actually write (default is a dry run)")
    ap.add_argument("--limit", type=int, default=None, help="cap documents resolved (sampling)")
    ap.add_argument("--user-id", type=int, default=None, required=True,
                    help="acting staff user id, recorded on every audit event")
    ap.add_argument("--show", type=int, default=40, help="detail rows to print")
    args = ap.parse_args(argv)

    # The caller must be a real staff user holding documents.edit; this script never invents scope.
    principal = Principal(args.user_id, "", "", frozenset({"documents.edit"}))
    result = apply_display_names(principal=principal, document_ids=args.document,
                                 safe_all=args.safe_all, dry_run=not args.apply, limit=args.limit)

    mode = "APPLIED" if not result["dry_run"] else "DRY RUN — nothing written"
    print(f"DOCUMENT DISPLAY NAME APPLY — {mode}")
    print(f"  {'considered':<34} {result['considered']}")
    for key, value in result["counts"].items():
        print(f"  {key:<34} {value}")
    print(f"\n  preview totals: {result['preview_totals']}")

    def block(title, outcome):
        rows = [r for r in result["rows"] if r["outcome"] == outcome][:args.show]
        if not rows:
            return
        print(f"\n{title} ({outcome})\n{'-' * (len(title) + len(outcome) + 3)}")
        for r in rows:
            print(f"  #{r['document_id']}  {r['current_filename']}")
            print(f"      -> {r['proposed_display_name']}")
            if r.get("current_display_name"):
                print(f"      existing display_name: {r['current_display_name']}")

    block("Applied" if not result["dry_run"] else "Would apply", APPLIED)
    block("Conflicts (left untouched)", CONFLICT_EXISTING_NAME)

    print("\noriginal_name, stored_name, storage_path, storage_uri and sha256 were not modified; "
          "no physical file was renamed or moved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
