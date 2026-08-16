"""Phase 5 — READ-ONLY inventory of UNSUPPORTED documents.

Reports every currently-UNSUPPORTED unassigned document and summarises by extension / failure reason /
source system. Runs no OCR and writes nothing.

Usage:
    python scripts/inventory_unsupported.py
    python scripts/inventory_unsupported.py --csv unsupported_inventory.csv
"""
from __future__ import annotations

import argparse
import csv

from app.services.document_unsupported import inventory


def main(argv=None):
    ap = argparse.ArgumentParser(description="READ-ONLY UNSUPPORTED-document inventory.")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--csv", default=None)
    args = ap.parse_args(argv)
    res = inventory(limit=args.limit)
    print("=" * 74)
    print(f"UNSUPPORTED INVENTORY (READ-ONLY) — TOTAL {res['total']}")
    print("=" * 74)
    print("By extension:")
    for k, v in res["by_extension"].items():
        print(f"  {v:4}  .{k}")
    print("By failure reason:")
    for k, v in res["by_reason"].items():
        print(f"  {v:4}  {k}")
    print("By source system:")
    for k, v in res["by_source"].items():
        print(f"  {v:4}  {k}")
    print("-" * 74)
    print("Document IDs:", [r["document_id"] for r in res["rows"]][:200])
    if args.csv:
        cols = ["document_id", "filename", "extension", "content_type", "source_system", "source_path",
                "extraction_method", "failure_reason", "file_exists", "extractor_exists_but_failed",
                "ocr_attempted", "ocr_cache_exists", "ocr_status"]
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh); w.writerow(cols)
            for r in res["rows"]:
                w.writerow([r.get(c) for c in cols])
        print(f"Wrote {res['total']} rows to {args.csv}")
    print("READ-ONLY: nothing written; no OCR run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
