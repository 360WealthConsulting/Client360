"""Phase 1 — READ-ONLY validation report for HIGH owner proposals (legacy backlog cleanup).

Reports every HIGH proposal with its evidence + provenance, runs contradiction checks, and separates the
clean/bulk-eligible HIGH set from the HIGH set that must go to manual review. Writes NOTHING and assigns
NO ownership. Full SSNs/TINs are never printed (evidence is already masked).

Usage (from the app root; app/.env provides DATABASE_URL):
    python scripts/validate_high_proposals.py                    # summary + samples
    python scripts/validate_high_proposals.py --csv high.csv     # also write a CSV of every HIGH row
    python scripts/validate_high_proposals.py --show all         # print every row (not just samples)
    python scripts/validate_high_proposals.py --ocr              # re-run OCR fallback (else use cache)
"""
from __future__ import annotations

import argparse
import csv

from app.services.document_high_validation import CONTRADICTION_CLASSES, validate_high_proposals


def _fmt_row(r):
    owner = f"{r['proposed_entity_type']} #{r['proposed_entity_id']} {r['proposed_entity_name']}"
    flags = ("CLEAN" if r["eligible"] else "EXCLUDED: " + ", ".join(r["contradictions"]))
    return (f"  #{r['document_id']} {r['filename']}  [{r['extraction_class']}:{r['extraction_method']}]\n"
            f"      src={r['source_system']!r} folder={r['source_path']!r}\n"
            f"      -> {owner}  {r['confidence']}  provenance={r['identity_provenance']}  "
            f"evidence={','.join(r['evidence_classes'])}\n"
            f"      {flags}")


def _write_csv(path, rows):
    cols = ["document_id", "filename", "source_system", "source_path", "extraction_class",
            "extraction_method", "proposed_entity_type", "proposed_entity_id", "proposed_entity_name",
            "confidence", "identity_provenance", "eligible", "evidence_classes", "contradictions"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            w.writerow([r["document_id"], r["filename"], r["source_system"], r["source_path"],
                        r["extraction_class"], r["extraction_method"], r["proposed_entity_type"],
                        r["proposed_entity_id"], r["proposed_entity_name"], r["confidence"],
                        r["identity_provenance"], r["eligible"], "|".join(r["evidence_classes"]),
                        "|".join(r["contradictions"])])


def main(argv=None):
    ap = argparse.ArgumentParser(description="READ-ONLY HIGH-proposal validation report.")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--csv", default=None, help="write a CSV of every HIGH row to this path")
    ap.add_argument("--show", choices=("samples", "clean", "excluded", "all"), default="samples")
    ap.add_argument("--sample", type=int, default=8)
    ap.add_argument("--ocr", action="store_true", help="re-run OCR fallback (default: use OCR cache)")
    args = ap.parse_args(argv)

    result = validate_high_proposals(limit=args.limit, ocr=args.ocr)
    print("=" * 78)
    print("PHASE 1 — HIGH PROPOSAL VALIDATION (READ-ONLY)")
    print("=" * 78)
    print(f"HIGH total:            {result['high_total']}")
    print(f"  native-text HIGH:    {result['native_high']}")
    print(f"  OCR-derived HIGH:    {result['ocr_high']}")
    print(f"HIGH clean/eligible:   {result['eligible']}")
    print(f"HIGH excluded (review):{result['excluded']}")
    print("-" * 78)
    print("Exclusion reason counts:")
    for cls in CONTRADICTION_CLASSES:
        n = result["reason_counts"].get(cls, 0)
        if n:
            print(f"  {cls:28} {n}")
    print("=" * 78)

    rows = result["rows"]
    if args.show == "clean":
        rows = [r for r in rows if r["eligible"]]
    elif args.show == "excluded":
        rows = [r for r in rows if not r["eligible"]]
    shown = rows if args.show == "all" else rows[: args.sample]
    for r in shown:
        print(_fmt_row(r))
    if args.show == "samples" and len(rows) > len(shown):
        print(f"  ...(+{len(rows) - len(shown)} more; use --show all or --csv)")

    if args.csv:
        _write_csv(args.csv, result["rows"])
        print(f"\nWrote {len(result['rows'])} HIGH rows to {args.csv}")
    print("\nREAD-ONLY: no ownership assigned; no document modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
