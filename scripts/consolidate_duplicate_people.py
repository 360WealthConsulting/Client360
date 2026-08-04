"""MDM-2 — consolidate duplicate canonical people via the MDM-1 merge engine.

Default is PREVIEW (no database changes). ``--apply`` performs merges through merge_people() for every
safe, unambiguous group; it is resumable (already-merged / vanished duplicates are skipped). Ambiguous
and blocked groups are always skipped. A CSV report is written for both modes.

Usage:
    python scripts/consolidate_duplicate_people.py                 # preview (default)
    python scripts/consolidate_duplicate_people.py --preview
    python scripts/consolidate_duplicate_people.py --apply
    python scripts/consolidate_duplicate_people.py --apply --report reports/mdm2_merge_report.csv
"""
import argparse
import sys

from app.services.mdm import consolidator


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="python scripts/consolidate_duplicate_people.py",
                                description="Consolidate duplicate canonical people (MDM-2).")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true", help="Report only, no changes (default).")
    mode.add_argument("--apply", action="store_true", help="Merge safe, unambiguous groups.")
    p.add_argument("--report", default="reports/mdm2_merge_report.csv", help="CSV report path.")
    args = p.parse_args(argv)

    apply = bool(args.apply)          # default (neither flag) → preview
    print(f"MDM-2 consolidation — mode: {'APPLY' if apply else 'PREVIEW (no changes)'}")
    summary = consolidator.consolidate(apply=apply, report_path=args.report,
                                       progress=lambda m: print("  " + m))
    print("-" * 60)
    for k in ("groups", "merged", "skipped", "ambiguous", "blocked", "failed"):
        print(f"  {k}: {summary[k]}")
    print(f"  report: {summary.get('report_path')}")
    print(f"  automatic-survivor rules: {consolidator.AUTOMATIC_SURVIVOR_RULE_COUNT}")
    if not apply:
        print("  (PREVIEW — no database changes were made)")
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
