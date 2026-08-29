"""Bounded recovery for the 2026-08-29 TaxDome merge-retirement incident.

DEFAULT IS DRY-RUN. A real run needs --apply plus both reviewed guards.

    python -m scripts.recover_taxdome_merge_retirements --plan \
        --incident-run dmx-27d47d01dc85 \
        --window-start "2026-08-29T16:34:20.492085-04:00" \
        --window-end   "2026-08-29T16:36:12.111980-04:00"

    python -m scripts.recover_taxdome_merge_retirements --apply \
        --incident-run ... --window-start ... --window-end ... \
        --expected-eligible 1532 --expected-plan-fingerprint <fingerprint>

The incident run id and window are REQUIRED: this is not a generic "restore every merge-retired
document" tool. Only documents passing all thirteen guards are touched, and only their status and
deleted_at are written. No file is read or written, no dependency is reassigned, the surviving
canonical document is never modified, and TaxDome sync is never invoked.
Output is plain ASCII for a CP1252 console.
"""
import argparse
import json
import sys
from datetime import datetime

from app.services.document_merge_recovery import (
    DEFAULT_BATCH_SIZE,
    GUARDS,
    RecoveryError,
)
from app.services.document_merge_recovery import (
    apply as apply_recovery,
)
from app.services.document_merge_recovery import (
    plan as build_plan,
)


def _plan_text(p: dict) -> str:
    L = ["=" * 78, "TAXDOME MERGE-RETIREMENT RECOVERY PLAN    READ-ONLY - NOTHING CHANGED",
         "=" * 78,
         f"  incident run        : {p['incident_run_id']}",
         f"  window              : {p['window_start']}  ..  {p['window_end']}",
         f"  source system       : {p['source_system']}",
         f"  plan fingerprint    : {p['plan_fingerprint']}",
         "",
         f"  population from audit : {p['population_from_audit']}",
         f"  ELIGIBLE              : {p['eligible_count']}",
         f"  REFUSED               : {p['refused_count']}",
         "", "  Refusals by guard (a candidate may fail several):"]
    if not p["refusals_by_guard"]:
        L.append("    (none)")
    for g, n in p["refusals_by_guard"].items():
        L.append(f"    {g:<42} {n:>6}")
    L += ["", "  Guards applied (ALL must pass):"]
    for i, g in enumerate(GUARDS, start=1):
        L.append(f"    {i:>2}. {g}")
    return "\n".join(L)


def _run_text(r: dict) -> str:
    dry = r["dry_run"]
    mode = {"DRY_RUN": "DRY RUN - NO DATABASE WRITE WAS ISSUED",
            "SUCCESS": "APPLIED - SUCCESS",
            "PARTIAL": "*** PARTIAL - SOME DOCUMENTS WERE NOT RESTORED ***",
            "FAILED": "FAILED - NOTHING COMMITTED"}[r["status"]]
    would = "would " if dry else ""
    L = ["=" * 78, f"TAXDOME MERGE-RETIREMENT RECOVERY    {mode}", "=" * 78,
         f"  recovery run        : {r['recovery_run_id']}",
         f"  incident run        : {r['incident_run_id']}",
         f"  plan fingerprint    : {r['plan_fingerprint']}",
         f"  wrote anything      : {r['wrote_anything']}",
         f"  filesystem mutations: {r['filesystem_mutations']}",
         "",
         f"  documents planned   : {r['documents_planned']}",
         f"  documents {would}restore  : "
         f"{r['documents_planned'] if dry else r['documents_restored']}",
         f"  documents refused   : {r['documents_refused']}",
         f"  documents failed    : {r['documents_failed']}",
         f"  documents not attempted : {r['documents_not_attempted']}",
         "",
         f"  FINAL STATUS        : {r['status']}   (exit {r['exit_code']})",
         ""]
    if r["failed_batch"]:
        f = r["failed_batch"]
        L += [f"  committed batches   : "
              f"{', '.join(str(b) for b in r['committed_batches']) or '(none)'}",
              f"  failed batch        : {f['batch']}  ({f['error']}: {f['detail']})",
              f"  documents committed : {r['documents_restored']}", ""]
    if r["refused"]:
        L.append("  REFUSED documents:")
        for x in r["refused"][:50]:
            L.append(f"    document {x['document_id']}  {x['refused']}")
            L.append(f"      {x['detail']}")
        if len(r["refused"]) > 50:
            L.append(f"    ... {len(r['refused']) - 50} more (use --output-json)")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m scripts.recover_taxdome_merge_retirements",
        description="Bounded recovery for the TaxDome merge-retirement incident. Dry-run default.")
    ap.add_argument("--incident-run", required=True, help="the merge run id to recover (required)")
    ap.add_argument("--window-start", required=True, help="ISO timestamp (required)")
    ap.add_argument("--window-end", required=True, help="ISO timestamp (required)")
    ap.add_argument("--source-system", default="TaxDome Drive")
    ap.add_argument("--plan", action="store_true", help="build and print a plan; changes nothing")
    ap.add_argument("--apply", action="store_true", help="restore; requires both --expected-*")
    ap.add_argument("--expected-eligible", type=int, default=None)
    ap.add_argument("--expected-plan-fingerprint", default=None)
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--output-json", metavar="PATH")
    args = ap.parse_args(argv)

    kw = {"run_id": args.incident_run,
          "window_start": datetime.fromisoformat(args.window_start),
          "window_end": datetime.fromisoformat(args.window_end),
          "source_system": args.source_system}
    try:
        if args.plan:
            out = build_plan(**kw)
            print(json.dumps(out, indent=2, default=str) if args.json else _plan_text(out))
        else:
            out = apply_recovery(
                apply_writes=args.apply, batch_size=args.batch_size,
                expected_eligible=args.expected_eligible,
                expected_plan_fingerprint=args.expected_plan_fingerprint, **kw)
            print(json.dumps(out, indent=2, default=str) if args.json else _run_text(out))
    except RecoveryError as exc:
        print(f"\n  REFUSED - nothing was written.\n  {exc}")
        return 2

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, default=str)
        print(f"\n  wrote {args.output_json}")
    if not args.plan and out["exit_code"] != 0:
        return out["exit_code"]
    if not args.apply:
        print("\n  (no --apply: no document was changed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
