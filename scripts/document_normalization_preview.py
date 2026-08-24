"""Document name normalization PREVIEW — READ-ONLY (SELECT only; writes nothing, renames nothing).

    python scripts/document_normalization_preview.py                  # full trusted population
    python scripts/document_normalization_preview.py --limit 2000     # bounded sample
    python scripts/document_normalization_preview.py --examples 50    # SAFE examples to print

Shows what the trusted documents COULD be displayed as, consistently, using only data Client360
already holds: original_name, category, and the person/household/organization owner. It adds no
column, modifies no document, renames no file, runs no OCR, and never touches a source file or
SharePoint/OneDrive.
"""
from __future__ import annotations

import argparse

from app.services.document_normalization_preview import BUCKETS, build_preview


def _block(title):
    print(f"\n{title}\n{'-' * len(title)}")


def _rows(rows, *, show_proposed=True, show_reason=False):
    for r in rows:
        print(f"  #{r['document_id']}  [{r['owner_type']}] {r['owner']}")
        print(f"      from: {r['current_filename']}")
        if show_proposed:
            print(f"      to:   {r['proposed_display_name']}")
        if show_reason:
            print(f"      why:  {r['reason']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only document naming preview (writes nothing).")
    ap.add_argument("--limit", type=int, default=None, help="cap documents reviewed (sampling)")
    ap.add_argument("--examples", type=int, default=50, help="SAFE before/after examples to print")
    args = ap.parse_args(argv)

    rep = build_preview(limit=args.limit, examples=args.examples)

    print("DOCUMENT NAME NORMALIZATION PREVIEW — READ-ONLY (nothing written, nothing renamed)")
    _block("Totals")
    print(f"  {'total trusted reviewed':<28} {rep['total_reviewed']}")
    for b in BUCKETS:
        n = rep["counts"][b]
        pct = n * 100 // max(rep["total_reviewed"], 1)
        print(f"  {b:<28} {n:>7}  ({pct}%)")
    print(f"  {'collisions':<28} {rep['collisions']}")

    _block("By owner type")
    for k, v in rep["by_owner_type"].items():
        print(f"  {str(k):<28} {v}")

    _block("By detected document type")
    for k, v in rep["by_document_type"].items():
        print(f"  {str(k):<28} {v}")

    _block("By source system")
    for k, v in rep["by_source_system"].items():
        print(f"  {str(k):<28} {v}")

    _block(f"SAFE examples (up to {args.examples}) — before -> after")
    _rows(rep["examples"]["SAFE"])

    _block("REVIEW examples (up to 25) — with reason")
    _rows(rep["examples"]["REVIEW"], show_reason=True)

    _block("UNCHANGED examples (up to 25) — existing name kept")
    _rows(rep["examples"]["UNCHANGED"], show_proposed=False, show_reason=True)

    _block("SKIP examples (up to 25) — with reason")
    _rows(rep["examples"]["SKIP"], show_proposed=False, show_reason=True)

    print("\nPREVIEW ONLY — no document was modified, renamed, reclassified, or written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
