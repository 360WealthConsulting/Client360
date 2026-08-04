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
    mode.add_argument("--apply", action="store_true", help="Merge every safe, unambiguous group.")
    mode.add_argument("--apply-clear-only", dest="apply_clear_only", action="store_true",
                      help="Strict first pass: merge only clear groups (materially-stronger survivor, "
                           "all duplicates empty shells, no warnings/blockers).")
    p.add_argument("--group", default=None, help="Limit to one exact (normalized) group name.")
    p.add_argument("--person-id", type=int, default=None, help="Limit to the group containing this id.")
    p.add_argument("--report", default="reports/mdm2_merge_report.csv", help="Pair-level CSV path.")
    p.add_argument("--group-summary", default="reports/mdm2_group_summary.csv",
                   help="Group-level CSV path.")
    args = p.parse_args(argv)

    apply = bool(args.apply)
    clear_only = bool(args.apply_clear_only)
    label = ("APPLY (clear-only)" if clear_only else "APPLY" if apply else "PREVIEW (no changes)")
    scope = (f" group='{args.group}'" if args.group else "") + (
        f" person_id={args.person_id}" if args.person_id else "")
    print(f"MDM-2 consolidation — mode: {label}{scope}")
    summary = consolidator.consolidate(
        apply=apply, apply_clear_only=clear_only, group_name=args.group, person_id=args.person_id,
        report_path=args.report, group_summary_path=args.group_summary,
        progress=lambda m: print("  " + m))
    print("-" * 60)
    for k in ("groups", "merged", "skipped", "ambiguous", "blocked", "failed",
              "clear_only_qualified"):
        print(f"  {k}: {summary[k]}")
    print(f"  pair report:  {summary.get('report_path')}")
    print(f"  group report: {summary.get('group_summary_path')}")
    print(f"  automatic-survivor rules: {consolidator.AUTOMATIC_SURVIVOR_RULE_COUNT}")
    if not apply:
        print("  (PREVIEW — no database changes were made)")
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
