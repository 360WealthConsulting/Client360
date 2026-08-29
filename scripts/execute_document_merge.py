"""Guarded executor for canonical document consolidation (ADR-072).

DEFAULT IS DRY-RUN. A real write needs --apply AND both expected-count guards.

    python -m scripts.execute_document_merge --plan
    python -m scripts.execute_document_merge --plan --output-json reports/plan.json
    python -m scripts.execute_document_merge                       # dry run, revalidates, writes 0
    python -m scripts.execute_document_merge --plan-file reports/plan.json
    python -m scripts.execute_document_merge --plan-file reports/plan.json --apply \
        --expected-safe-partitions 1999 --expected-retirement-rows 2550

The executor acts on OWNERSHIP PARTITIONS, never on SHA groups. Only a partition preview() has
classified SAFE_AUTO_MERGE can run; SHARED_CONTENT, REVIEW_REQUIRED and BLOCKED have no path.
Retirement is the platform soft-delete (status='deleted' + deleted_at). No file is ever deleted,
moved, renamed or re-read, and no filesystem or external system is touched.

All console output is plain ASCII so a CP1252 Windows console needs no PYTHONIOENCODING.
"""
import argparse
import json
import sys

from app.services.document_merge_execute import (
    DEFAULT_BATCH_SIZE,
    MergeExecutionError,
)
from app.services.document_merge_execute import (
    apply as apply_merges,
)
from app.services.document_merge_execute import (
    plan as build_plan,
)


def _plan_text(p: dict) -> str:
    L = ["=" * 78,
         "DOCUMENT MERGE EXECUTION PLAN    READ-ONLY - NO CHANGES WERE MADE",
         "=" * 78,
         f"  survivor rule      : {p['survivor_rule']}",
         f"  eligibility        : documents.{p['eligibility']}",
         "",
         f"  SAFE_AUTO_MERGE partitions       : {p['safe_partitions']}",
         f"  rows to retire                   : {p['rows_to_retire']}",
         f"  proposed reassignments           : {p['proposed_reassignments']}",
         f"  provenance tuples to preserve    : {p['provenance_tuples_to_preserve']}",
         ""]
    if p["reassignments_by_table"]:
        L.append("  Reassignments by dependent table:")
        for k, n in p["reassignments_by_table"].items():
            L.append(f"    - {k}: {n}")
        L.append("")
    L.append("  Refused (never executable):")
    if not p["refused_partitions"]:
        L.append("    (none)")
    for k, n in p["refused_partitions"].items():
        L.append(f"    {k:<20} {n}")
    t = p["preview_totals"]
    L += ["",
          "  Reconciles to the preview:",
          f"    merge_partitions_safe            : {t['merge_partitions_safe']}",
          f"    rows_eligible_for_retirement     : {t['rows_eligible_for_retirement']}",
          f"    shared_content_groups (never run): {t['shared_content_groups']}",
          "",
          f"  plan matches preview: "
          f"{'YES' if p['safe_partitions'] == t['merge_partitions_safe'] else 'NO'}"]
    return "\n".join(L)


def _run_text(r: dict) -> str:
    dry = r["dry_run"]
    # The heading is the STATUS the service derived - never re-derived here, so the text and the
    # result artifact can never disagree.
    mode = {
        "DRY_RUN": "DRY RUN - NO DATABASE WRITE WAS ISSUED",
        "SUCCESS": "APPLIED - SUCCESS",
        "PARTIAL": "*** PARTIAL - SOME PARTITIONS DID NOT APPLY ***",
        "FAILED": "FAILED - NOTHING COMMITTED",
    }[r["status"]]
    would = "would " if dry else ""
    L = ["=" * 78, f"DOCUMENT MERGE EXECUTION    {mode}", "=" * 78,
         f"  run id              : {r['run_id']}",
         f"  wrote anything      : {r['wrote_anything']}",
         f"  retirement          : {r['retirement_mechanism']}",
         f"  batch size          : {r['batch_size']}",
         "",
         f"  partitions planned  : {r['partitions_planned']}",
         f"  partitions applied  : {r['partitions_applied'] if not dry else r['partitions_prepared']}",
         f"  partitions refused  : {r['partitions_refused']}",
         f"  partitions failed   : {r['partitions_failed']}",
         f"  partitions not attempted : {r['partitions_not_attempted']}",
         "",
         f"  planned retirement rows  : {r['planned_retirement_rows']}",
         f"  actual retirement rows   : "
         f"{r['would_retire_rows'] if dry else r['rows_retired']}",
         f"  dependent-row reassignments: "
         f"{sum(r['would_reassign_by_table'].values()) if dry else r['reassignments_total']}",
         "",
         f"  FINAL STATUS        : {r['status']}   (exit {r['exit_code']})",
         ""]
    if r["failed_batch"]:
        f = r["failed_batch"]
        L += [f"  committed batches   : "
              f"{', '.join(str(b) for b in r['committed_batches']) or '(none)'}",
              f"  failed batch        : {f['batch']}  ({f['error']}: {f['detail']})",
              f"  partitions committed: {r['partitions_applied']}",
              f"  retirement rows committed: {r['rows_committed']}",
              ""]
        if r["status"] == "PARTIAL":
            L += ["  WARNING: batches commit independently. The batches listed above are DURABLE.",
                  "  The failed batch rolled back completely; later batches were never attempted.",
                  "  Re-plan against current state before retrying - do NOT replay the old plan.",
                  ""]
    moved = r["would_reassign_by_table"] if dry else r["reassigned_by_table"]
    if moved:
        L.append(f"  Rows that {would}be reassigned by dependent table:")
        for k, n in moved.items():
            L.append(f"    - {k}: {n}")
        L.append("")
    drops = r["would_delete_by_table"] if dry else r["deleted_by_table"]
    if drops:
        L.append(f"  Redundant rows that {would}be removed (survivor already carries them):")
        for k, n in drops.items():
            L.append(f"    - {k}: {n}")
        L.append("")
    if r["refused"]:
        L.append("  REFUSED partitions:")
        for x in r["refused"][:50]:
            L.append(f"    {x['sha256'][:16]}...  {x['refused']}")
            L.append(f"      {x['detail']}")
        if len(r["refused"]) > 50:
            L.append(f"    ... {len(r['refused']) - 50} more (use --output-json)")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m scripts.execute_document_merge",
        description="Guarded ownership-scoped document merge executor. Dry-run by default.")
    ap.add_argument("--plan", action="store_true",
                    help="generate and print an execution plan; makes no changes")
    ap.add_argument("--plan-file", metavar="PATH",
                    help="apply/dry-run this exact saved plan (stale plans are REJECTED, never "
                         "silently regenerated)")
    ap.add_argument("--apply", action="store_true",
                    help="perform real writes; requires both --expected-* guards below")
    ap.add_argument("--expected-safe-partitions", type=int, default=None,
                    help="the SAFE partition count you reviewed; a mismatch stops before any write")
    ap.add_argument("--expected-retirement-rows", type=int, default=None,
                    help="the retirement row count you reviewed; a mismatch stops before any write")
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                    help=f"partitions per transaction (default {DEFAULT_BATCH_SIZE})")
    ap.add_argument("--limit", type=int, default=None, help="only the first N duplicate groups")
    ap.add_argument("--json", action="store_true", help="emit the result as JSON")
    ap.add_argument("--output-json", metavar="PATH", help="write the full result to PATH")
    args = ap.parse_args(argv)

    plan_doc = None
    if args.plan_file:
        with open(args.plan_file, encoding="utf-8") as fh:
            plan_doc = json.load(fh)

    if args.plan:
        plan_doc = plan_doc or build_plan(limit=args.limit)
        out = plan_doc
        print(json.dumps(out, indent=2, default=str) if args.json else _plan_text(out))
    else:
        try:
            out = apply_merges(plan_doc=plan_doc, apply_writes=args.apply,
                               batch_size=args.batch_size, limit=args.limit,
                               expected_safe_partitions=args.expected_safe_partitions,
                               expected_retirement_rows=args.expected_retirement_rows)
        except MergeExecutionError as exc:
            print(f"\n  REFUSED - nothing was written.\n  {exc}")
            return 2
        print(json.dumps(out, indent=2, default=str) if args.json else _run_text(out))
        if out["exit_code"] != 0:
            if args.output_json:
                with open(args.output_json, "w", encoding="utf-8") as fh:
                    json.dump(out, fh, indent=2, default=str)
                print(f"\n  wrote {args.output_json}")
            return out["exit_code"]

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, default=str)
        print(f"\n  wrote {args.output_json}")
    if not args.apply:
        print("\n  (no --apply: no database write was issued)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
