"""Guarded canonical document-storage relocation (PLAN -> COPY -> VERIFY -> DB REPOINT).

DEFAULT IS DRY-RUN. A real run needs --apply AND all three reviewed guards.

    python -m scripts.relocate_document_storage --plan --output-json reports/relocation-plan.json
    python -m scripts.relocate_document_storage --plan-file reports/relocation-plan.json
    python -m scripts.relocate_document_storage --plan-file reports/relocation-plan.json --apply \
        --expected-safe-rows 1234 --expected-safe-bytes 5678901 \
        --expected-plan-fingerprint <fingerprint>

Set CLIENT360_DATA_ROOT (e.g. D:\\360PlusData) so storage_paths resolves the canonical roots.
Source files are never deleted, moved or renamed. Output is plain ASCII for a CP1252 console.
"""
import argparse
import json
import sys

from app.services.document_relocation import (
    DEFAULT_BATCH_SIZE,
    RelocationError,
    data_root,
)
from app.services.document_relocation import (
    apply as apply_relocation,
)
from app.services.document_relocation import (
    plan as build_plan,
)


def _plan_text(p: dict) -> str:
    s = p["summary"]
    L = ["=" * 78, "DOCUMENT STORAGE RELOCATION PLAN    READ-ONLY - NOTHING WAS CHANGED", "=" * 78,
         f"  CLIENT360_DATA_ROOT : {data_root() or '(not set - legacy roots apply)'}",
         f"  plan fingerprint    : {p['plan_fingerprint']}",
         f"  hashed any file     : {p['hashed_any_file']}   (plan uses DB metadata only)",
         "", "  Canonical destination roots:"]
    for src, root in sorted(p["canonical_roots"].items()):
        L.append(f"    {src:<12} {root}")
    L += ["", "  " + "-" * 74, "  CLASSIFICATION", "  " + "-" * 74,
          f"  rows examined       : {s['rows_examined']}",
          f"  SAFE                : {s['SAFE']}",
          f"  REVIEW_REQUIRED     : {s['REVIEW_REQUIRED']}",
          f"  BLOCKED             : {s['BLOCKED']}",
          f"  ALREADY_CANONICAL   : {s['ALREADY_CANONICAL']}",
          f"  EMPTY_URI           : {s['EMPTY_URI']}",
          "",
          f"  bytes to relocate   : {s['safe_bytes_total']}",
          f"  destination collisions   : {s['destination_collisions']}",
          f"  conflicting hashes       : {len(s['conflicting_hashes'])}",
          f"  missing required metadata: {len(s['missing_required_metadata'])}",
          "", "  Counts per current root:"]
    for k, n in s["counts_per_current_root"].items():
        L.append(f"    {k:<40} {n:>8}")
    L += ["", "  Counts per proposed destination root:"]
    for k, n in s["counts_per_proposed_destination_root"].items():
        L.append(f"    {k:<40} {n:>8}")
    return "\n".join(L)


def _run_text(r: dict) -> str:
    dry = r["dry_run"]
    if dry:
        mode = "DRY RUN - NO DATABASE OR FILESYSTEM WRITE WAS ISSUED"
    elif r["partial_apply"]:
        mode = "*** PARTIAL APPLY - SOME BATCHES ARE COMMITTED ***"
    elif r["failed_batch"]:
        mode = "FAILED - NOTHING COMMITTED"
    else:
        mode = "APPLIED"
    would = "would " if dry else ""
    L = ["=" * 78, f"DOCUMENT STORAGE RELOCATION    {mode}", "=" * 78,
         f"  run id              : {r['run_id']}",
         f"  plan fingerprint    : {r['plan_fingerprint']}",
         f"  wrote anything      : {r['wrote_anything']}",
         f"  filesystem mutations: {r['filesystem_mutations']}",
         f"  source files deleted: {r['source_files_deleted']}   (no delete path exists)",
         f"  batch size          : {r['batch_size']}",
         "",
         f"  rows planned        : {r['rows_planned']}",
         f"  rows verified       : {r['rows_verified']}",
         f"  rows {would}relocate     : {r['rows_relocated'] if not dry else r['rows_verified']}",
         f"  rows refused        : {r['rows_refused']}",
         f"  bytes {would}relocate    : "
         f"{r['bytes_would_relocate'] if dry else r['bytes_relocated']}",
         ""]
    if r["failed_batch"]:
        f = r["failed_batch"]
        L += [f"  committed batches   : "
              f"{', '.join(str(b) for b in r['committed_batches']) or '(none)'}",
              f"  failed batch        : {f['batch']}  ({f['error']}: {f['detail']})",
              f"  rows committed      : {r['rows_relocated']}",
              ""]
        if r["partial_apply"]:
            L += ["  WARNING: batches commit independently. The batches above are DURABLE.",
                  "  Source files were NOT deleted, so every committed row is still rollbackable",
                  "  from the audit evidence. Re-plan before retrying; do not replay this plan.",
                  ""]
    if r["refused"]:
        L.append("  REFUSED rows:")
        for x in r["refused"][:50]:
            L.append(f"    document {x['document_id']}  {x['refused']}")
            L.append(f"      {x['detail']}")
        if len(r["refused"]) > 50:
            L.append(f"    ... {len(r['refused']) - 50} more (use --output-json)")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m scripts.relocate_document_storage",
        description="Guarded canonical document-storage relocation. Dry-run by default.")
    ap.add_argument("--plan", action="store_true", help="build and print a plan; changes nothing")
    ap.add_argument("--plan-file", metavar="PATH",
                    help="apply/dry-run this exact saved plan (a stale plan is REJECTED)")
    ap.add_argument("--apply", action="store_true",
                    help="perform real copies and DB repoints; requires all --expected-* guards")
    ap.add_argument("--expected-safe-rows", type=int, default=None)
    ap.add_argument("--expected-safe-bytes", type=int, default=None)
    ap.add_argument("--expected-plan-fingerprint", default=None)
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    ap.add_argument("--limit", type=int, default=None, help="only the first N document rows")
    ap.add_argument("--json", action="store_true", help="emit the result as JSON")
    ap.add_argument("--output-json", metavar="PATH", help="write the full result to PATH")
    args = ap.parse_args(argv)

    plan_doc = None
    if args.plan_file:
        with open(args.plan_file, encoding="utf-8") as fh:
            plan_doc = json.load(fh)

    if args.plan:
        out = plan_doc or build_plan(limit=args.limit)
        print(json.dumps(out, indent=2, default=str) if args.json else _plan_text(out))
    else:
        try:
            out = apply_relocation(
                plan_doc=plan_doc, apply_writes=args.apply, batch_size=args.batch_size,
                limit=args.limit, expected_safe_rows=args.expected_safe_rows,
                expected_safe_bytes=args.expected_safe_bytes,
                expected_plan_fingerprint=args.expected_plan_fingerprint)
        except RelocationError as exc:
            print(f"\n  REFUSED - nothing was copied or written.\n  {exc}")
            return 2
        print(json.dumps(out, indent=2, default=str) if args.json else _run_text(out))
        if out["failed_batch"]:
            return 3 if out["partial_apply"] else 4

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, default=str)
        print(f"\n  wrote {args.output_json}")
    if not args.apply:
        print("\n  (no --apply: no file was copied and no row was changed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
