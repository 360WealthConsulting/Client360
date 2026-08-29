"""Read-only preview of canonical DOCUMENT consolidation (ADR-072). Makes NO changes.

Database-only: it reads documents, their stored SHA-256 hashes and their dependent rows. It never
touches the filesystem, a storage backend, SharePoint, TaxDome or OCR, and it never writes.

Usage:
    python scripts/preview_document_merge.py                # whole corpus
    python scripts/preview_document_merge.py --limit 50     # first 50 duplicate groups
    python scripts/preview_document_merge.py --json         # machine-readable
    python scripts/preview_document_merge.py --show 10      # per-group detail for the first 10
"""
import argparse
import json
import sys

from app.services.document_merge import BLOCKED, REVIEW, SAFE, preview


def _summary(r: dict) -> str:
    L = ["=" * 78,
         "CANONICAL DOCUMENT MERGE PREVIEW    READ-ONLY - NO CHANGES WERE MADE",
         "=" * 78,
         f"  survivor rule      : {r['survivor_rule']}",
         f"  eligibility        : documents.{r['eligibility']}",
         f"  dependencies known : {r['dependencies_checked']}",
         ""]
    if r["unregistered_dependencies"]:
        L.append(f"  !! UNREGISTERED DEPENDENCIES: {', '.join(r['unregistered_dependencies'])}")
        L.append("     Groups touching these are BLOCKED - add a strategy before consolidating.")
        L.append("")
    L += [f"  duplicate SHA groups          : {r['total_duplicate_groups']}",
          f"  document rows in those groups : {r['total_document_rows_in_groups']}",
          f"  excess duplicate rows         : {r['excess_duplicate_rows']}",
          "",
          f"  {SAFE:<18}: {r['safe_auto_merge_groups']}",
          f"  {REVIEW:<18}: {r['review_required_groups']}",
          f"  {BLOCKED:<18}: {r['blocked_groups']}",
          "",
          f"  proposed reassignments        : {r['total_proposed_reassignments']}",
          f"  rows eventually retired       : {r['total_rows_eventually_retired']}",
          f"  provenance rows seen          : {r['provenance_rows_seen']}",
          f"  provenance tuples preserved   : {r['provenance_tuples_preserved']}"]
    if r["reassignments_by_table"]:
        L.append("\n  Reassignments by dependent table:")
        for k, n in r["reassignments_by_table"].items():
            L.append(f"    - {k}: {n}")

    rr = r["reasons"]
    L.append("")
    L.append("  " + "-" * 74)
    L.append("  WHY THE NON-SAFE GROUPS ARE NOT SAFE")
    L.append("  " + "-" * 74)
    L.append("  'primary' is mutually exclusive (one per group, so totals reconcile);")
    L.append("  'containing' counts every group carrying the reason and therefore overlaps.")
    for label, rows in (("BLOCKERS  -> BLOCKED", rr["blockers"]),
                        ("CONFLICTS -> REVIEW_REQUIRED", rr["conflicts"]),
                        ("ADVISORIES (do not affect classification)", rr["advisories"])):
        L.append(f"\n  {label}")
        if not rows:
            L.append("    (none)")
        for row in rows:
            L.append(f"    {row['code']:<38} primary {row['groups_primary']:>6}   "
                     f"containing {row['groups_containing']:>6}")
            L.append(f"      {row['description']}")
            if row["example_document_ids"]:
                L.append(f"      example document ids: {row['example_document_ids']}")

    pt = rr["primary_totals"]
    L.append("")
    L.append(f"  primary BLOCKED total        : {pt['blocked']} (classified {r['blocked_groups']})")
    L.append(f"  primary REVIEW_REQUIRED total: {pt['review_required']} "
             f"(classified {r['review_required_groups']})")
    L.append(f"  reconciles                   : {'YES' if rr['reconciles'] else 'NO'}")
    if rr["unreported_codes"]:
        L.append(f"  !! codes with no taxonomy entry: {rr['unreported_codes']}")
    return "\n".join(L)


def _group(g: dict) -> str:
    L = ["", "-" * 78,
         f"  [{g['classification']}]  survivor {g['proposed_survivor']}  "
         f"<- duplicates {g['duplicate_document_ids']}",
         f"    rows {g['row_count']}  excess {g['excess_rows']}  "
         f"reassignments {g['total_reassignments']}",
         f"    provenance: {g['provenance']['rows']} rows -> "
         f"{g['provenance']['preserved_after_merge']} distinct tuples "
         f"({', '.join(g['provenance']['source_systems']) or 'none'})"]
    for b in g["blockers"]:
        L.append(f"    BLOCKER  {b['code']}: {b.get('detail', b['description'])}")
    for c in g["conflicts"]:
        L.append(f"    CONFLICT {c['code']}: {c.get('detail', c['description'])}")
    if g["reassignments_required"]:
        L.append("    would reassign:")
        for k, info in sorted(g["reassignments_required"].items()):
            L.append(f"      - {k}: {info['rows']} ({info['strategy']}, ON DELETE {info['delete_rule']})")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read-only canonical document merge preview.")
    ap.add_argument("--limit", type=int, default=None, help="only the first N duplicate groups")
    ap.add_argument("--show", type=int, default=0, help="print per-group detail for the first N")
    ap.add_argument("--json", action="store_true", help="emit the full report as JSON")
    args = ap.parse_args(argv)

    report = preview(limit=args.limit)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0
    print(_summary(report))
    for g in report["groups"][:args.show]:
        print(_group(g))
    print("\n  (preview only - no document, row, file or migration was changed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
