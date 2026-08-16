"""READ-ONLY batch analysis of all genuinely-unassigned Client360 documents.

Runs the standard document pipeline (extract -> classify -> match -> score -> route) over every document
whose person_id, household_id and organization_id are all NULL, excluding the six permanent rejects. It
WRITES NOTHING and assigns NO ownership — it produces the counts and a sanitized per-document breakdown an
admin uses to decide what to confirm. Full SSNs/TINs are never printed (evidence is already masked).

Usage (from the app root; app/.env provides DATABASE_URL):
    python scripts/analyze_unassigned_documents.py                 # counts + a sample of each route
    python scripts/analyze_unassigned_documents.py --limit 500     # cap documents analyzed
    python scripts/analyze_unassigned_documents.py --route HIGH --sample 50   # inspect HIGH results
    python scripts/analyze_unassigned_documents.py --doc 459       # analyze specific document id(s)
"""
from __future__ import annotations

import argparse

from app.services.document_pipeline import ROUTES, analyze_document, run_batch


def _print_detail(d):
    owner = (f"{d.get('proposed_entity_type')} #{d.get('proposed_entity_id')} "
             f"{d.get('proposed_entity_name')}") if d.get("proposed_entity_id") else "—"
    dt = f"{d.get('doc_type') or '—'}" + (f"/{d['year']}" if d.get("year") else "")
    print(f"  #{d.get('document_id')} {d.get('filename')}  [{dt}]  folder={d.get('source_folder')!r}")
    print(f"      route={d.get('route')}  confidence={d.get('confidence')}  proposed={owner}  "
          f"({d.get('extraction_method')})")
    for e in (d.get("evidence") or [])[:6]:
        print(f"      {e}")
    if d.get("best_candidates"):
        cands = ", ".join(f"{c.get('name')} (#{c.get('person_id')})" for c in d["best_candidates"])
        print(f"      competing: {cands}")
    if d.get("error"):
        print(f"      ERROR: {d['error']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="READ-ONLY unassigned-document pipeline batch analysis.")
    ap.add_argument("--limit", type=int, default=None, help="cap documents analyzed")
    ap.add_argument("--route", choices=ROUTES, default=None, help="only print details for this route")
    ap.add_argument("--sample", type=int, default=5, help="details to print per route (default 5)")
    ap.add_argument("--doc", type=int, nargs="*", help="analyze only these document id(s)")
    ap.add_argument("--ocr", action="store_true",
                    help="OCR image-only/scanned docs via the existing backend (populates the OCR text "
                         "cache; writes no ownership). Requires the OCR backend installed on the host.")
    args = ap.parse_args(argv)

    if args.doc:
        print("=" * 74)
        for did in args.doc:
            _print_detail({**analyze_document(did, ocr=args.ocr)})
        print("=" * 74)
        print("READ-ONLY (ownership): nothing assigned; no document modified.")
        return 0

    result = run_batch(limit=args.limit, include_details=True, ocr=args.ocr)
    c = result["counts"]
    s = result["stats"]
    print("=" * 74)
    print("UNASSIGNED DOCUMENT PIPELINE — BATCH ANALYSIS" + ("  (OCR ON)" if args.ocr else "  (native)"))
    print("=" * 74)
    print(f"TOTAL:                {result['total']}")
    print(f"HIGH:                 {c['HIGH']}")
    print(f"MEDIUM:               {c['MEDIUM']}")
    print(f"AMBIGUOUS:            {c['AMBIGUOUS']}")
    print(f"NEW_CLIENT_CANDIDATE: {c['NEW_CLIENT_CANDIDATE']}")
    print(f"NO_MATCH:             {c['NO_MATCH']}")
    print(f"UNSUPPORTED:          {c['UNSUPPORTED']}")
    print(f"ERROR:                {c['ERROR']}")
    print("-" * 74)
    print(f"OCR-extracted:        {s['ocr_extracted']}")
    print(f"OCR with identity:    {s['ocr_with_identity']}")
    print(f"Unsupported remain:   {s['unsupported_remaining']}")
    print("=" * 74)

    routes = [args.route] if args.route else list(ROUTES)
    for route in routes:
        rows = [d for d in result["details"] if d.get("route") == route]
        if not rows:
            continue
        shown = rows[: args.sample]
        print(f"\n--- {route} ({len(rows)}) — showing {len(shown)} ---")
        for d in shown:
            _print_detail(d)
    print("\n" + "=" * 74)
    print("No ownership assigned; no document modified." + (
        "  (OCR text cache may have been populated.)" if args.ocr else "  (read-only)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
