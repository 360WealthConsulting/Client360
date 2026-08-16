"""Phase 4 — READ-ONLY NO_MATCH context-analysis report.

Classifies every current NO_MATCH document into A/B/C/D/E using source context (folder mappings, resolved
neighbour documents, folder-resolution decisions, folder-name matches) WITHOUT re-running OCR and WITHOUT
assigning ownership or creating anything. Prints the bucket counts, folder statistics, top folders, and
top reasons, and optionally writes a CSV of every classified document.

Usage (from the app root; app/.env provides DATABASE_URL):
    python scripts/analyze_nomatch_context.py
    python scripts/analyze_nomatch_context.py --csv nomatch_context.csv
    python scripts/analyze_nomatch_context.py --bucket CONTEXT_HIGH --sample 30
"""
from __future__ import annotations

import argparse
import csv

from app.services.document_nomatch_analysis import BUCKETS, analyze_nomatch


def _write_csv(path, rows):
    cols = ["document_id", "filename", "source_system", "source_path", "extraction_method", "bucket",
            "proposed_owner_type", "proposed_owner_id", "proposed_owner_name", "contextual_confidence",
            "folder_mapping", "evidence", "contradicting_evidence", "supporting_documents"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c) if c != "supporting_documents" else
                        "|".join(str(x) for x in (r.get("supporting_documents") or [])) for c in cols])


def main(argv=None):
    ap = argparse.ArgumentParser(description="READ-ONLY NO_MATCH context analysis.")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--bucket", choices=BUCKETS, default=None, help="print sample rows for this bucket")
    ap.add_argument("--sample", type=int, default=15)
    args = ap.parse_args(argv)

    res = analyze_nomatch(limit=args.limit)
    c = res["letters"]
    print("=" * 78)
    print("PHASE 4 — NO_MATCH CONTEXT ANALYSIS (READ-ONLY)")
    print("=" * 78)
    print(f"TOTAL NO_MATCH:          {res['total']}")
    print(f"A CONTEXT_HIGH:          {c['A']}")
    print(f"B CONTEXT_LIKELY:        {c['B']}")
    print(f"C CONFLICT:              {c['C']}")
    print(f"D GENERAL_OR_UNRESOLVED: {c['D']}")
    print(f"E POSSIBLE_NEW_ENTITY:   {c['E']}")
    print("-" * 78)
    fs = res["folder_stats"]
    print(f"unique source folders:            {fs['unique_folders']}")
    print(f"folders uniquely canonical-mapped:{fs['unique_mapped']}")
    print(f"folders mixed/ambiguous:          {fs['mixed_or_ambiguous']}")
    print("-" * 78)
    print("Top folders by NO_MATCH documents:")
    for f in res["top_folders"]:
        print(f"  {f['nomatch_docs']:4}  {f['folder']}")
    print("-" * 78)
    print("Top reasons documents stayed unresolved:")
    for r in res["reasons"]:
        print(f"  {r['count']:4}  {r['reason']}")
    print("=" * 78)

    if args.bucket:
        rows = [r for r in res["rows"] if r["bucket"] == args.bucket][: args.sample]
        print(f"\n--- {args.bucket} sample ({len(rows)}) ---")
        for r in rows:
            owner = (f"{r['proposed_owner_type']} #{r['proposed_owner_id']} {r['proposed_owner_name']}"
                     if r["proposed_owner_id"] else "—")
            print(f"  #{r['document_id']} {r['filename']}  folder={r['source_path']!r}")
            print(f"      -> {owner}  [{r['folder_mapping']}]  {r['evidence']}")
            if r["contradicting_evidence"]:
                print(f"      contradiction: {r['contradicting_evidence']}")
            if r["supporting_documents"]:
                print(f"      supported by resolved docs: {r['supporting_documents'][:10]}")

    if args.csv:
        _write_csv(args.csv, res["rows"])
        print(f"\nWrote {len(res['rows'])} rows to {args.csv}")
    print("\nREAD-ONLY: no ownership assigned; no entity created; no document modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
