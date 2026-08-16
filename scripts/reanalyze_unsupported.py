"""Phase 5 — targeted re-analysis of ONLY the UNSUPPORTED documents (BEFORE vs AFTER).

Re-runs the currently-UNSUPPORTED documents (or the given ids) through the SAME pipeline with the OCR
fallback enabled + the new docx/ics extractors, and reports the resulting bucket counts. Assigns no
ownership, creates nothing, moves/deletes no files — the only side effect is populating the OCR text
cache for these targeted documents.

Usage:
    python scripts/reanalyze_unsupported.py
    python scripts/reanalyze_unsupported.py --doc 458 459 ...
"""
from __future__ import annotations

import argparse

from app.services.document_unsupported import reanalyze


def main(argv=None):
    ap = argparse.ArgumentParser(description="Targeted UNSUPPORTED re-analysis (OCR + new extractors).")
    ap.add_argument("--doc", type=int, nargs="*", help="specific document ids (default: all UNSUPPORTED)")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    res = reanalyze(doc_ids=args.doc, limit=args.limit)
    c = res["after_counts"]
    print("=" * 74)
    print("UNSUPPORTED RE-ANALYSIS — BEFORE vs AFTER")
    print("=" * 74)
    print(f"UNSUPPORTED before:      {res['before_unsupported']}")
    print("After:")
    print(f"  HIGH:                  {c.get('HIGH', 0)}")
    print(f"  MEDIUM:                {c.get('MEDIUM', 0)}")
    print(f"  AMBIGUOUS:             {c.get('AMBIGUOUS', 0)}")
    print(f"  NEW_CLIENT_CANDIDATE:  {c.get('NEW_CLIENT_CANDIDATE', 0)}")
    print(f"  NO_MATCH:              {c.get('NO_MATCH', 0)}")
    print(f"  UNSUPPORTED remaining: {c.get('UNSUPPORTED', 0)}")
    print(f"  ERROR:                 {c.get('ERROR', 0)}")
    print("-" * 74)
    print(f"documents newly yielding usable text: {res['newly_text']}")
    print(f"documents yielding identity evidence: {res['newly_identity']}")
    print(f"remaining unsupported: {res['remaining_count']}")
    for r in res["remaining"][:100]:
        print(f"  #{r['document_id']}  reason={r['reason']}")
    print("=" * 74)
    print("No ownership assigned; no entity created; no file moved/deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
