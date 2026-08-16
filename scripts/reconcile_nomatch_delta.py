"""READ-ONLY reconciliation of the NO_MATCH count difference between the batch report and Phase 4.

The batch report (scripts/analyze_unassigned_documents.py) buckets by the pipeline ROUTE, which has a
separate UNSUPPORTED bucket for documents with no usable extracted text. Phase 4
(scripts/analyze_nomatch_context.py -> analyze_nomatch) selects on the matching engine's CONFIDENCE, and
an empty-text document has confidence NO_MATCH (there is no "UNSUPPORTED" confidence). So documents the
batch called UNSUPPORTED are counted as NO_MATCH by Phase 4.

This script classifies every unassigned document BOTH ways (reusing document_pipeline.analyze_document,
which returns the proposal confidence AND the route) and reports, for the Phase-4 NO_MATCH set, how many
the batch had as route=NO_MATCH vs route=UNSUPPORTED — i.e. the exact delta, with document IDs and the
extraction method that made each one UNSUPPORTED. It re-runs no OCR (reads the cache) and writes nothing.

Usage:
    python scripts/reconcile_nomatch_delta.py
    python scripts/reconcile_nomatch_delta.py --csv nomatch_delta.csv
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter

from app.db import engine
from app.services.document_high_validation import _unassigned_ids
from app.services.document_pipeline import analyze_document


def reconcile(*, limit=None):
    phase4_nomatch = 0
    both_nomatch = 0            # route == NO_MATCH (the batch's NO_MATCH set)
    delta_rows = []             # route == UNSUPPORTED but confidence == NO_MATCH (the +delta)
    by_method: Counter = Counter()
    with engine.connect() as conn:
        ids = _unassigned_ids(conn, limit=limit)
        for did in ids:
            r = analyze_document(did, conn=conn, ocr=False)
            if not r.get("eligible") or r.get("confidence") != "NO_MATCH":
                continue
            phase4_nomatch += 1
            route = r.get("route")
            if route == "UNSUPPORTED":
                by_method[r.get("extraction_method")] += 1
                delta_rows.append({"document_id": did, "filename": r.get("filename"),
                                   "extraction_method": r.get("extraction_method"),
                                   "batch_route": "UNSUPPORTED", "phase4_bucket": "NO_MATCH"})
            else:
                both_nomatch += 1
    return {"phase4_nomatch": phase4_nomatch, "batch_nomatch": both_nomatch,
            "delta_unsupported": len(delta_rows), "by_method": dict(by_method), "delta_rows": delta_rows}


def main(argv=None):
    ap = argparse.ArgumentParser(description="READ-ONLY NO_MATCH 319->368 reconciliation.")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--csv", default=None)
    args = ap.parse_args(argv)
    res = reconcile(limit=args.limit)
    print("=" * 74)
    print("NO_MATCH RECONCILIATION (READ-ONLY)")
    print("=" * 74)
    print(f"Phase-4 NO_MATCH (confidence==NO_MATCH):     {res['phase4_nomatch']}")
    print(f"  of which batch route == NO_MATCH:          {res['batch_nomatch']}")
    print(f"  of which batch route == UNSUPPORTED (delta):{res['delta_unsupported']}")
    print("-" * 74)
    print("Delta by extraction method (why they had no text):")
    for method, n in sorted(res["by_method"].items(), key=lambda kv: -kv[1]):
        print(f"  {n:4}  {method}")
    print("-" * 74)
    print(f"Reconciliation: batch NO_MATCH {res['batch_nomatch']} + batch UNSUPPORTED "
          f"{res['delta_unsupported']} = Phase-4 NO_MATCH {res['phase4_nomatch']}")
    print("Delta document IDs:", [r["document_id"] for r in res["delta_rows"]][:200])
    print("=" * 74)
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["document_id", "filename", "extraction_method", "batch_route", "phase4_bucket"])
            for r in res["delta_rows"]:
                w.writerow([r["document_id"], r["filename"], r["extraction_method"],
                            r["batch_route"], r["phase4_bucket"]])
        print(f"Wrote {len(res['delta_rows'])} delta rows to {args.csv}")
    print("READ-ONLY: nothing written; no ownership changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
